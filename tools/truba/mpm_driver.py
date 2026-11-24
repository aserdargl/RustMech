"""Kratos-side TPMS MPM driver, shared by Windows and Slurm."""

import csv
import json
import math
from pathlib import Path

import numpy as np
import KratosMultiphysics as KM
import KratosMultiphysics.MPMApplication as MPM
from KratosMultiphysics.MPMApplication.mpm_analysis import MpmAnalysis


def parameters(case):
    return KM.Parameters(json.dumps({
        'problem_data': dict(problem_name='TPMS compression', parallel_type='OpenMP', echo_level=0,
                             start_time=0.0, end_time=case['mpm']['duration_ms'] * 1e-3),
        'solver_settings': dict(
            solver_type='Dynamic', model_part_name='MPM_Material', domain_size=3, echo_level=0,
            analysis_type='linear', time_integration_method='explicit', scheme_type='forward_euler',
            stress_update='usf', is_fix_explicit_mp_on_grid_edge=True, compressible=True, pressure_dofs=False,
            model_import_settings=dict(input_type='mdpa', input_filename='Body'),
            grid_model_import_settings=dict(input_type='mdpa', input_filename='Grid'),
            material_import_settings=dict(materials_filename='Materials.json'),
            time_stepping=dict(time_step=case['dt_s']),
            problem_domain_sub_model_part_list=['Parts_body', 'Parts_grid'],
            processes_sub_model_part_list=[],
        ),
        'processes': {},
    }))


def plane_force(overlap, closing_speed, stiffness, damping):
    """A frictionless, unilateral penalty force for one material-point domain."""
    return max(0.0, stiffness * overlap + damping * closing_speed) if overlap > 0 else 0.0


class Compression(MpmAnalysis):
    def __init__(self, case):
        super().__init__(KM.Model(), parameters(case))
        self.case = case
        self.step = 0
        self.curve = [(0., 0., 0., 0., 0.)]
        self.maximum_overlap = 0.
        self.peak_upper = 0.
        self.stroke = case['mpm']['stroke_mm'] * 1e-3
        self.height = case['geometry']['size'] * 1e-3
        self.duration = case['mpm']['duration_ms'] * 1e-3
        self.spacing = case['point_mm'] * 1e-3
        self.grid_spacing = case['grid_mm'] * 1e-3
        lateral_padding = max(2, math.ceil(round(self.height / self.grid_spacing) / 3)) * self.grid_spacing
        self.padding = (lateral_padding, lateral_padding, 2 * self.grid_spacing)
        self.stiffness = case['mpm']['young_mpa'] * 1e6 * self.spacing
        self.body_acceleration = KM.Array3([0., 0., 0.])
        self.contact_points = set()

    def Initialize(self):
        super().Initialize()
        self.body = self._GetSolver().GetComputingModelPart()
        self.info = self.body.ProcessInfo
        self.mass = next(iter(self.body.Elements)).CalculateOnIntegrationPoints(MPM.MP_MASS, self.info)[0]
        expected_mass = self.case['mpm']['density_kg_m3'] * self.spacing**3
        if self.body.NumberOfElements() != self.case['points'] or not math.isclose(self.mass, expected_mass):
            raise RuntimeError('Kratos did not generate the expected material points and mass.')
        self.damping = 0.2 * math.sqrt(self.stiffness * self.mass)

    def KeepAdvancingSolutionLoop(self):
        return self.step < self.case['steps']

    def InitializeSolutionStep(self):
        super().InitializeSolutionStep()
        self.step += 1
        fraction = self.step / self.case['steps']
        # Cosine ramp starts and ends at rest. Evaluate contact at the imposed
        # platen position, with the material state from the preceding step.
        self.travel = self.stroke * (0.5 - 0.5 * math.cos(math.pi * fraction))
        plate_speed = -0.5 * math.pi * self.stroke / self.duration * math.sin(math.pi * fraction)
        upper_plane = self.height - self.travel
        half = 0.5 * self.spacing
        self.upper_force = self.lower_force = 0.
        contact = set()
        for point in self.body.Elements:
            z = point.CalculateOnIntegrationPoints(MPM.MP_COORD, self.info)[0][2]
            if not math.isfinite(z):
                raise RuntimeError('A material point became non-finite. Review the load and grid spacing.')
            upper_overlap = z + half - upper_plane
            lower_overlap = half - z
            if max(upper_overlap, lower_overlap) <= 0:
                if point.Id in self.contact_points:
                    self.body_acceleration[2] = 0.
                    point.SetValuesOnIntegrationPoints(MPM.MP_VOLUME_ACCELERATION, [self.body_acceleration], self.info)
                continue
            contact.add(point.Id)
            vz = point.CalculateOnIntegrationPoints(MPM.MP_VELOCITY, self.info)[0][2]
            if not math.isfinite(vz):
                raise RuntimeError('Non-finite contact velocity. Review the load and grid spacing.')
            upper = plane_force(upper_overlap, vz - plate_speed, self.stiffness, self.damping)
            lower = plane_force(lower_overlap, -vz, self.stiffness, self.damping)
            self.body_acceleration[2] = (lower - upper) / self.mass
            point.SetValuesOnIntegrationPoints(MPM.MP_VOLUME_ACCELERATION, [self.body_acceleration], self.info)
            self.upper_force += upper
            self.lower_force += lower
            self.maximum_overlap = max(self.maximum_overlap, upper_overlap, lower_overlap)
        self.contact_points = contact
        self.peak_upper = max(self.peak_upper, self.upper_force)

    def FinalizeSolutionStep(self):
        super().FinalizeSolutionStep()
        if self.body.NumberOfElements() != self.case['points']:
            raise RuntimeError('Material points left the background grid. Review the imposed stroke and duration.')
        if self.maximum_overlap > self.spacing:
            raise RuntimeError('Platen penetration exceeded one material-point spacing. Increase the loading duration or refine the grid.')
        steps = self.case['steps']
        if self.step % max(1, steps // 250) and self.step != steps:
            return
        kinetic = 0.
        for point in self.body.Elements:
            volume = point.CalculateOnIntegrationPoints(MPM.MP_VOLUME, self.info)[0]
            speed = point.CalculateOnIntegrationPoints(MPM.MP_VELOCITY, self.info)[0]
            if not math.isfinite(volume) or volume <= 0 or not all(map(math.isfinite, speed)):
                raise RuntimeError('Invalid material-point state. Review the loading duration and grid spacing.')
            kinetic += 0.5 * self.mass * sum(component**2 for component in speed)
        self.curve.append((self.duration * self.step / steps, self.travel * 1000,
                           self.upper_force, self.lower_force, kinetic))
        if self.step == steps or self.step % (max(1, steps // 250) * 10) == 0:
            print(f'MPM progress: Solving MPM · {self.step}/{steps} steps', flush=True)

    def save(self):
        outputs = {}
        for name, variable, scale in (('coordinates_mm', MPM.MP_COORD, 1000.),
                                      ('displacement_mm', MPM.MP_DISPLACEMENT, 1000.),
                                      ('cauchy_stress_mpa', MPM.MP_CAUCHY_STRESS_VECTOR, 1e-6)):
            outputs[name] = scale * np.asarray([list(point.CalculateOnIntegrationPoints(variable, self.info)[0])
                                                for point in self.body.Elements])
        if any(not np.all(np.isfinite(values)) for values in outputs.values()):
            raise RuntimeError('Non-finite MPM output was produced.')
        padding = np.asarray(self.padding)
        coordinates = outputs['coordinates_mm'] * 1e-3
        if np.any(coordinates < -padding) or np.any(coordinates > self.height + padding):
            raise RuntimeError('Material points left the background grid on the final step.')
        np.savez_compressed('mpm_final_particles.npz', **outputs)
        with Path('mpm_force_displacement.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(('time_s', 'displacement_mm', 'upper_force_n', 'lower_force_n', 'kinetic_energy_j'))
            writer.writerows(self.curve)
        summary = dict(self.case, format='rustmech-mpm', version=1, material='compressible Neo-Hookean',
                       platen_contact='frictionless unilateral penalty with fixed particle half-spacing',
                       maximum_overlap_mm=self.maximum_overlap * 1000, peak_upper_force_n=self.peak_upper,
                       final_upper_force_n=self.upper_force, final_lower_force_n=self.lower_force)
        Path('mpm_run.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')


def main():
    KM.Logger.GetDefaultOutput().SetSeverity(KM.Logger.Severity.WARNING)
    analysis = Compression(json.loads(Path('mpm_input.json').read_text(encoding='utf-8')))
    analysis.Run()
    analysis.save()


if __name__ == '__main__':
    main()
