"""Portable Kratos worker. No SSH credentials or Windows dependencies."""
import json
import os
import socket
from pathlib import Path
import traceback

import numpy as np
from kratos_self_contact import contact_partition
import KratosMultiphysics as KM
import KratosMultiphysics.StructuralMechanicsApplication as SMA
import KratosMultiphysics.ContactStructuralMechanicsApplication as CSMA
import KratosMultiphysics.ConstitutiveLawsApplication
import KratosMultiphysics.LinearSolversApplication
from KratosMultiphysics.StructuralMechanicsApplication.structural_mechanics_analysis import StructuralMechanicsAnalysis
from KratosMultiphysics.integration_values_extrapolation_to_nodes_process import IntegrationValuesExtrapolationToNodesProcess


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, indent=2)+"\n")
    temporary.replace(path)


class SelfContactSearch:
    """Supply stable, topology-aware roles to the native mortar search."""
    def __init__(self,native,part,payload):
        self.native=native
        self.conditions=list(part.GetSubModelPart('Contact.ContactSub2').Conditions)
        self.mapping=payload['self_contact_nodes']
        self.locked={}
        self.candidate_pairs=0

    def ExecuteInitializeSolutionStep(self):
        self.native.ClearMortarConditions()
        geometry=[list(condition.GetGeometry()) for condition in self.conditions]
        points=[[[n.X,n.Y,n.Z] for n in face] for face in geometry]
        original=[[int(self.mapping.get(str(n.Id),n.Id)) for n in face] for face in geometry]
        sizes=[max(n.GetSolutionStepValue(KM.NODAL_H) for n in face) for face in geometry]
        roles,self.locked,pairs=contact_partition(points,original,sizes,self.locked)
        self.candidate_pairs=len(pairs)
        for condition,face,role in zip(self.conditions,geometry,roles):
            condition.Set(KM.SLAVE,bool(role));condition.Set(KM.MASTER,not bool(role))
            for node in face:
                node.Set(KM.SLAVE,bool(role));node.Set(KM.MASTER,not bool(role))
        self.native.CreatePointListMortar()
        self.native.ExecuteInitializeSolutionStep()

    def ExecuteFinalizeSolutionStep(self):
        self.native.ExecuteFinalizeSolutionStep()


class CompressionAnalysis(StructuralMechanicsAnalysis):
    def __init__(self, payload, parameters):
        self.payload = payload
        self.settings = payload["settings"]
        self.experiment=payload.get('experiment','compression')
        self.load_sign=payload.get('load_sign',-1.)
        self.results = Path("results")
        self.results.mkdir(exist_ok=True)
        self.manifest = {"schema_version": 1, "mesh_digest": payload["mesh_digest"], "complete": False,
                         "hostname": socket.gethostname(), "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                         "mesh_verified": False, "steps": [], "stress_definition": "von Mises of area-averaged nodal Cauchy stress tensors extrapolated from integration points"}
        super().__init__(KM.Model(), parameters)
        self.manifest.update(experiment=self.experiment,test_profile=self.settings.get('test_profile','custom'),load_sign=self.load_sign,fixture_model=payload.get('test_profile',{}).get('fixture_model','Legacy compression platens'))
        if 'effective_tolerances' in payload:self.manifest['effective_tolerances']=payload['effective_tolerances']

    def ModifyInitialGeometry(self):
        part = self.model["Structure"]
        mesh = np.load("mesh_input.npz", allow_pickle=False)
        ids, points, cells = mesh["ids"], mesh["points"], mesh["cells"]
        actual = np.array([[part.Nodes[int(n)].X0, part.Nodes[int(n)].Y0, part.Nodes[int(n)].Z0] for n in ids])
        if not np.allclose(points,actual,rtol=0,atol=1e-12):
            raise ValueError("Imported specimen coordinates differ from RustMech mesh")
        for i, cell in enumerate(cells):
            if [n.Id for n in part.Elements[i+1].GetGeometry()] != ids[cell].tolist():
                raise ValueError("Imported specimen connectivity differs from RustMech mesh")
        self.manifest["mesh_verified"] = True
        if self.settings['self_contact_mode']!='disabled':
            # Seed the destination tree. Native automatic pairing resets these
            # flags each step, then assigns the self-contact master/slave roles.
            for condition in part.GetSubModelPart('SelfSurface').Conditions:
                condition.Set(KM.MASTER,True)
        constraint_id = 1
        skin_nodes=self.payload.get('self_contact_nodes',{})
        for duplicate,original in skin_nodes.items():
            for variable in (KM.DISPLACEMENT_X,KM.DISPLACEMENT_Y,KM.DISPLACEMENT_Z):
                part.CreateNewMasterSlaveConstraint('LinearMasterSlaveConstraint',constraint_id,
                    part.Nodes[int(original)],variable,part.Nodes[int(duplicate)],variable,1.,0.)
                constraint_id+=1
        bottom = self.payload["plate_nodes"]["BottomPlaten"]
        top = self.payload["plate_nodes"]["TopPlaten"]
        pilot = part.Nodes[self.payload["pilot_node"]]
        for nid in bottom:
            for var in ((KM.DISPLACEMENT_Z,) if self.experiment=='flexure' else (KM.DISPLACEMENT_X, KM.DISPLACEMENT_Y, KM.DISPLACEMENT_Z)):
                part.Nodes[nid].Fix(var)
        for nid in top + [pilot.Id]:
            for var in (KM.DISPLACEMENT_X, KM.DISPLACEMENT_Y):
                if self.experiment!='flexure' or nid==pilot.Id:part.Nodes[nid].Fix(var)
            if self.settings["loading_mode"] == "displacement":
                part.Nodes[nid].Fix(KM.DISPLACEMENT_Z)
        if self.settings["loading_mode"] == "force":
            for i,nid in enumerate(top):
                part.CreateNewMasterSlaveConstraint("LinearMasterSlaveConstraint", constraint_id+i, pilot, KM.DISPLACEMENT_Z,
                                                  part.Nodes[nid], KM.DISPLACEMENT_Z, 1., 0.)
        else:
            # The pilot is bookkeeping only in displacement control.
            pilot.Fix(KM.DISPLACEMENT_Z)
        bottom_ids = [n.Id for n in part.GetSubModelPart("BottomSpecimen").Nodes]
        if self.experiment=='tension':return
        if self.experiment=='flexure':
            bottom_ids=bottom
            p=self.payload['test_profile']
            length,width,_=p['dimensions']
            anchors=[]
            for x in ((length-p['span'])/2,(length+p['span'])/2):
                anchors.append(min(bottom_ids,key=lambda n:(part.Nodes[n].X0-x)**2+(part.Nodes[n].Y0-width/2)**2))
            part.Nodes[anchors[0]].Fix(KM.DISPLACEMENT_X)
            for nid in anchors:part.Nodes[nid].Fix(KM.DISPLACEMENT_Y)
            return
        center = np.mean(points,axis=0)
        anchor = min(bottom_ids, key=lambda n: (part.Nodes[n].X0-center[0])**2 + (part.Nodes[n].Y0-center[1])**2)
        needs_stabilization = self.settings['kratos_contact_method']=='penalty' or not any(self.settings[side+"_platen_contact"] == "frictional" and self.settings[side+"_platen_friction_coeff"] > 0 for side in ("bottom","top"))
        if needs_stabilization:
            part.Nodes[anchor].Fix(KM.DISPLACEMENT_X)
            part.Nodes[anchor].Fix(KM.DISPLACEMENT_Y)
        other = max(bottom_ids, key=lambda n: (part.Nodes[n].X0-part.Nodes[anchor].X0)**2 + (part.Nodes[n].Y0-part.Nodes[anchor].Y0)**2)
        var = KM.DISPLACEMENT_Y if abs(part.Nodes[other].X0-part.Nodes[anchor].X0) >= abs(part.Nodes[other].Y0-part.Nodes[anchor].Y0) else KM.DISPLACEMENT_X
        if needs_stabilization:
            part.Nodes[other].Fix(var)

    def ApplyBoundaryConditions(self):
        super().ApplyBoundaryConditions()
        part = self.model["Structure"]
        factor = float(part.ProcessInfo[KM.TIME])
        if self.settings["loading_mode"] == "displacement":
            for nid in self.payload["plate_nodes"]["TopPlaten"]+[self.payload["pilot_node"]]:
                part.Nodes[nid].SetSolutionStepValue(KM.DISPLACEMENT_Z, self.load_sign*self.settings["compression"] * factor)
        else:
            part.Nodes[self.payload["pilot_node"]].SetSolutionStepValue(SMA.POINT_LOAD_Z, self.load_sign*self.settings["applied_force"] * factor)

    def ModifyBeforeSolutionLoop(self):
        part=self.model['Structure']
        if self.settings['self_contact_mode']!='disabled':
            part=self.model['Structure']
            coefficient=self.settings['self_friction_coeff'] if self.settings['self_contact_mode']=='frictional' else 0.
            if not np.isclose(part.GetProperties(102)[KM.FRICTION_COEFFICIENT],coefficient,rtol=0,atol=1e-12):
                raise RuntimeError('The requested self-contact friction coefficient was overwritten.')
            for node in part.GetSubModelPart('SelfSurface').Nodes:node.SetValue(KM.FRICTION_COEFFICIENT,coefficient)
            self.manifest['self_contact_coefficient']=coefficient
            # Skin-only nodes have no incident volume element. FindNodalH
            # leaves a sentinel there, which otherwise makes every OBB overlap.
            for duplicate,original in self.payload.get('self_contact_nodes',{}).items():
                part.Nodes[int(duplicate)].SetSolutionStepValue(KM.NODAL_H,
                    part.Nodes[int(original)].GetSolutionStepValue(KM.NODAL_H))
            for nodes in self.payload['plate_nodes'].values():
                if self.experiment in ('tension','flexure'):continue
                points=np.array([[part.Nodes[nid].X0,part.Nodes[nid].Y0,part.Nodes[nid].Z0] for nid in nodes])
                length=float(np.linalg.norm(points[:,None,:]-points[None,:,:],axis=2).max())
                for nid in nodes:part.Nodes[nid].SetSolutionStepValue(KM.NODAL_H,length)
            for process in self._GetListOfProcesses():
                searches=getattr(process,'search_utility_list',{})
                if '2' in searches:
                    count=len(self.payload['self_contact_surface']['faces'][0])
                    constructor=getattr(CSMA,f'AdvancedContactSearchProcess3D{count}N')
                    search_parameters=process._create_search_parameters('2')
                    search_parameters.RemoveValue('simple_search')
                    native=constructor(part,search_parameters,process._get_properties_pair('2'))
                    native.CreatePointListMortar();native.InitializeMortarConditions()
                    searches['2']=SelfContactSearch(native,part,self.payload)
            self.contact_output_nodes=list(part.GetSubModelPart('SelfSurface').Nodes)
            positions={int(n):i for i,n in enumerate(self.payload['specimen_node_ids'])}
            self.contact_output_indices=np.array([positions[int(self.payload['self_contact_nodes'].get(str(n.Id),n.Id))] for n in self.contact_output_nodes])
        self.extrapolate = IntegrationValuesExtrapolationToNodesProcess(self.model, KM.Parameters(json.dumps({
            "model_part_name": "Structure.Specimen", "list_of_variables": ["CAUCHY_STRESS_VECTOR"],
            "extrapolate_non_historical": True, "area_average": True})))
        self.extrapolate.ExecuteBeforeSolutionLoop()

    def RunSolutionLoop(self):
        # Do not let AnalysisStage silently finalize a nonconverged load step.
        while self.time < self.end_time - 1e-12:
            solver = self._GetSolver()
            self.time = min(solver.AdvanceInTime(self.time), self.end_time)
            self.InitializeSolutionStep()
            if not self.SolveSolutionStep():
                raise RuntimeError(f"Kratos did not converge at load factor {self.time:.9g}")
            actual_time=float(self.model['Structure'].ProcessInfo[KM.TIME])
            if abs(actual_time-self.time)>1e-8:
                raise RuntimeError(f'Kratos stopped at load factor {actual_time:.9g}, requested {self.time:.9g}')
            contact_state=None
            contact_fields={}
            if self.settings['self_contact_mode']!='disabled':
                part=self.model['Structure']
                mapping=self.payload.get('self_contact_nodes',{})
                nodes=self.contact_output_nodes
                active=[node for node in nodes if node.Is(KM.ACTIVE)]
                tangents=[float(np.linalg.norm(node.GetValue(CSMA.AUGMENTED_TANGENT_CONTACT_PRESSURE))) for node in active]
                normals=[abs(float(node.GetValue(CSMA.AUGMENTED_NORMAL_CONTACT_PRESSURE))) for node in active]
                mu=self.settings['self_friction_coeff'] if self.settings['self_contact_mode']=='frictional' else 0.
                contact_state={'active_nodes':len({int(mapping.get(str(n.Id),n.Id)) for n in active}),
                    'active_interface_nodes':len(active),
                    'maximum_pressure':max(normals,default=0.),
                    'maximum_tangential_pressure':max(tangents,default=0.),
                    'coulomb_limit_ratio':max((t/(mu*n) for t,n in zip(tangents,normals) if mu*n>1e-12),default=0.),
                    'computing_pairs':part.GetSubModelPart('ComputingContact.ComputingContactSub2').NumberOfConditions()}
                for key,variable in (('contact_pressure',CSMA.AUGMENTED_NORMAL_CONTACT_PRESSURE),('contact_tangential_pressure',CSMA.AUGMENTED_TANGENT_CONTACT_PRESSURE)):
                    field=np.zeros(len(self.payload['specimen_node_ids']))
                    values=[float(np.linalg.norm(node.GetValue(variable))) if node.Is(KM.ACTIVE) else 0. for node in self.contact_output_nodes]
                    np.maximum.at(field,self.contact_output_indices,values)
                    contact_fields[key]=field
            self.FinalizeSolutionStep()
            self.extrapolate.ExecuteFinalizeSolutionStep()
            part = self.model["Structure"]
            ids = np.asarray(self.payload["specimen_node_ids"],dtype=np.int64)
            displacement = np.array([list(part.Nodes[int(n)].GetSolutionStepValue(KM.DISPLACEMENT)) for n in ids])
            stress = np.array([list(part.Nodes[int(n)].GetValue(KM.CAUCHY_STRESS_VECTOR)) for n in ids])
            if stress.shape != (len(ids),6) or not np.isfinite(stress).all() or not np.isfinite(displacement).all():
                raise RuntimeError("Invalid or missing Kratos nodal solution fields")
            xx,yy,zz,xy,yz,xz = stress.T
            vm = np.sqrt(np.maximum(0, .5*((xx-yy)**2+(yy-zz)**2+(zz-xx)**2)+3*(xy**2+yz**2+xz**2)))
            bottom,top = [sum(part.Nodes[n].GetSolutionStepValue(KM.REACTION_Z) for n in self.payload["plate_nodes"][side]) for side in ("BottomPlaten","TopPlaten")]
            if self.settings["loading_mode"] == "force":
                top = self.load_sign*self.settings["applied_force"] * self.time
            uz = float(np.mean([part.Nodes[n].GetSolutionStepValue(KM.DISPLACEMENT_Z) for n in self.payload["plate_nodes"]["TopPlaten"]]))
            if self.settings['loading_mode']=='displacement' and not np.isclose(uz,self.load_sign*self.settings['compression']*self.time,rtol=1e-6,atol=1e-9):
                raise RuntimeError('The requested platen displacement was not reached in this substep.')
            if (self.settings['self_contact_mode']!='disabled' or self.experiment!='compression') and abs(bottom+top)>.01*max(abs(bottom),abs(top),1e-9):
                    raise RuntimeError('Solution failed the independent fixture force-balance check.')
            name = f"step_{len(self.manifest['steps'])+1:06d}.npz"
            np.savez_compressed(self.results/name, ids=ids, displacement=displacement, equivalent_stress=vm,**contact_fields)
            self.manifest["steps"].append({"file":name,"time":self.time,"top_displacement":uz,"bottom_reaction":bottom,"top_reaction":top})
            if contact_state is not None:self.manifest['steps'][-1]['self_contact']=contact_state
            write_json(self.results/"manifest.json",self.manifest)
            print(f"RUSTMECH_STEP={len(self.manifest['steps'])} LOAD={self.time:.9g}",flush=True)
        self.manifest["complete"] = True
        write_json(self.results/"manifest.json",self.manifest)


if __name__ == "__main__":
    analysis = None
    try:
        payload = json.loads(Path("input.json").read_text())
        parameters = KM.Parameters(Path("ProjectParameters.json").read_text())
        analysis = CompressionAnalysis(payload, parameters)
        analysis.Run()
    except BaseException as error:
        Path("results").mkdir(exist_ok=True)
        manifest = analysis.manifest if analysis is not None else {"schema_version":1,"complete":False,"steps":[]}
        manifest["error"] = str(error)
        write_json(Path("results/manifest.json"),manifest)
        Path("results/solver_error.txt").write_text(traceback.format_exc())
        raise
