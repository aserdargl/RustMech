# RustMech

RustMech, TPMS yapıları ve deney numuneleri için geometri önizleme, sonlu eleman
ağı üretimi ve analiz araçları sunan bir masaüstü uygulamasıdır. Mekanik analiz,
OpenFOAM ile akış (CFD) ve eşlenik ısı transferi (CHT) çalışma alanları aynı
proje dosyasında bir araya gelir. Desteklenen hesaplar TRUBA'ya gönderilebilir
ve Slurm işleri uygulamadan izlenebilir.

## Windows kurulumu

[RustMech Releases](https://github.com/aserdargl/RustMech-Releases/releases)
sayfasından istediğiniz sürümün `RustMech-<sürüm>-Setup.exe` dosyasını indirin
ve çalıştırın.

## Kaynak koddan çalıştırma

Depoda kayıtlı geliştirme ortamı Python 3.14.10 kullanır. Proje klasöründe temel
bağımlılıkları yükleyip `desktop_entry.py` giriş noktasını çalıştırın.

```powershell
python -m pip install -r requirements-base.txt
python desktop_entry.py
```

TRUBA bağlantısı, TET4/TET10 ağ üretimi ve ANSYS MAPDL için sırasıyla
`requirements-truba.txt`, `requirements-tet10.txt` ve `requirements-mapdl.txt`
dosyalarını `python -m pip install -r` ile yükleyin.

## Windows kurulum dosyasını derleme

Derleme ortamında PyInstaller ile temel, TET4/TET10, TRUBA ve MAPDL
bağımlılıkları bulunmalıdır. Proje klasöründe önce `dist\RustMech` paketini
oluşturun, ardından Inno Setup derleyicisiyle kurulum EXE'sini üretin.

```powershell
.\Build-Exe.ps1
ISCC.exe "/DAppVersion=<sürüm>" "/DOutputDir=<çıktı-klasörü>" packaging\RustMech.iss
```

`<sürüm>` yerine sayısal yayımlama sürümünü, `<çıktı-klasörü>` yerine kurulum
EXE'sinin yazılacağı klasörün tam yolunu yazın.

## Testleri çalıştırma

Aynı Python ortamına `pytest` yükleyin. `core` ekran gerektirmeyen testleri,
`gui` masaüstü oturumu gerektiren testleri, `all` ise iki grubu da çalıştırır.

```powershell
python tools\run_tests.py core
python tools\run_tests.py gui
python tools\run_tests.py all
```

RustMech, GPL-3.0-or-later ile lisanslanır. [License](#license) bölümüne ve [LICENSE](LICENSE) dosyasına bakın.

## MD Hazırlama

**ESEN → MD Hazırlama** sekmesinde periyodik tablodaki elemente sağ tıklayıp
**Ekle**’yi seçin. Listedeki her elementin atom yüzdesini girin ve toplamın
%100 olduğunu kontrol edin. Hedef atom sayısını belirleyin ve MEAM
kütüphanesini ve parametre dosyasını seçin.
Dosyalarda bir `pair_coeff` örneği varsa program MEAM element sırasını oradan
alır. Boş kalırsa parametre dosyasındaki indekslerin izlediği sırayı girin.

**Kristal tipi** alanında BCC tek kristal veya BCC polikristal seçin. Tek kristal
önizlemesi hedefe en yakın dengeli BCC hücresini gösterir. Polikristalde tane
sayısını da girin. Kübik kutunun kenarı yaklaşık `a × (N / 2)^(1/3)`, tane
boyutu ölçeği ise kutu kenarının tane sayısının küpköküne bölümüdür. Atomsk
periyodik Voronoi tanelerini oluşturduktan sonra gerçek atom sayısı ve bileşim
paketin `model.json` dosyasına yazılır. Aynı tohumla belirlenen tane konumları
ve yönelimleri `grain-nodes.txt` içinde saklanır.

**Parametreler nasıl seçilir?** bölümündeki hesaplayıcı, BCC (110) XRD düzlem
aralığından `a = √2 × d₁₁₀` ile başlangıç kafes parametresini bulur. Örneğin
`d₁₁₀ = 2,4000 Å` için `a = 3,3941 Å`. Aynı bileşim, BCC fazı ve hedef
sıcaklık için MEAM doğrulaması yaparken küçük bir periyodik tek kristali
sıfır basınçta gevşetin. Hedef sıcaklıkta NPT dengelemesi sonrasında ortalama
kutu uzunluğunu o eksendeki hücre tekrar sayısına bölün.

**Paketi hazırla**, Atomsk’ı bu bilgisayarda çalıştırır ve paket yolunu gösterir.
**Hazırla ve TRUBA’da çalıştır**, E20 şablonuyla minimizasyon, dengeleme, çekme
ve CNA işini gönderir. Şablon 3,9 Å sabit CNA kesim yarıçapı kullanır.
Farklı kafes parametrelerinde CNA sonuçlarını yorumlamadan önce kesim
yarıçapını doğrulayın. İşi ve sonuçları **ESEN iş geçmişi** üzerinden izleyin.

[Atomsk polikristal kılavuzu](https://atomsk.univ-lille.fr/doc/en/mode_polycrystal.html) ·
[LAMMPS kutu gevşetme](https://docs.lammps.org/fix_box_relax.html) ·
[LAMMPS CNA](https://docs.lammps.org/stable/compute_cna_atom.html)

## ESEN ile LAMMPS çalıştırma

Üst çubuktan **ESEN**'i açın. LAMMPS betiğini ve kullandığı veri, potansiyel veya
`include` dosyalarını **Dosya yükle…** ile seçin ya da girdi listesine sürükleyip
bırakın. Tek dosya veya bir gruptaki tek `in.*` / `*.in` dosyası ana betik olarak
seçilir. Diğer durumlarda ana dosyayı listeden seçip **Betik olarak seç** düğmesine
basın. **LAMMPS'i çalıştır** düğmesiyle işi başlatın.

İlk yüklemede dosyaların ortak klasörü iş klasörü olur. Betikteki göreli yollar
bu klasöre göre çözülür. Gerekirse **İş klasörü…** ile kökü belirleyin ve
**İş klasörünün tamamını ekle** ile alt klasörler dahil tüm dosyaları kopyalayın.
Her çalıştırmada ESEN girdilerin bir kopyasını saklar.

- **Bu bilgisayar** hedefi için `lmp` ve birden çok MPI süreci kullanıyorsanız
  LAMMPS derlemesiyle uyumlu `mpiexec` gerekir. ESEN, Windows'ta MS-MPI'yi arar.
  Farklı bir kurulum kullanıyorsanız program ve başlatıcı alanlarına tam yollarını yazın.
- **TRUBA** hedefi için RustMech'in **TRUBA** düğmesiyle bağlanın. Kuyruğu, hesabı,
  bellek ve süreyi, MPI süreç sayısını ve LAMMPS modülünü kontrol edin. ESEN ilk
  sürümde tek düğümlü CPU işi gönderir. Kişisel bir Linux kurulumu için modül
  alanını boş bırakıp çalıştırılabilir dosyanın yolunu girebilirsiniz. Betikte
  Linux'ta da çözülebilen göreli yollar kullanın.

**LAMMPS'i çalıştır** düğmesinden önce komutu veya Slurm betiğini önizleyebilirsiniz.
Başarılı TRUBA işlerinin çıktıları ve günlükleri, bağlantı açıkken yerel iş
klasöründeki `Results/ESEN-<iş-kimliği>` dizinine otomatik indirilir. Çıktıların
alt klasörleri korunur. Aktarım kesilirse **İş geçmişi**'nden işi seçip
**İşi yenile** ile tekrar deneyin. Dosyaları geçmişten tek tek de indirebilirsiniz.
RustMech yeniden açıldığında TRUBA'ya tekrar bağlanıp aynı işi seçerek izlemeye
devam edin. Aynı girdileri diğer hedefte çalıştırmak için
geçmişteki işi seçip **Girdileri yeniden kullan** düğmesine basın ve hedefi değiştirin.

### Klasörden TRUBA’ya toplu gönderim

Hazır Slurm betikleriyle çalışmak için TRUBA hedefini seçin ve **Folder**
ile `run_*.slurm` dosyalarını içeren proje klasörünü açın. Çalıştıracağınız
betikleri işaretleyin ve **Çalıştır** düğmesine basın. ESEN proje girdilerini
TRUBA’ya aktarır ve her betiği kendi klasöründeki göreli yollarla ayrı bir
Slurm işi olarak gönderir. Aynı aktarım birden fazla işte kullanılır.
Çalışan işlerin ayrı çalışma kopyaları vardır.

Dosya listesinde Ctrl ile tek tek, Shift ile aralık seçin. Boşluk tuşu
seçili çalıştırılabilir betiklerin kutularını birlikte işaretler veya temizler.
**İş ayarları** dosyaları kendi klasörlerinin altında gösterir. Ada veya uzantıya
göre filtreleyebilir ya da sıralayabilirsiniz. İşaretli dosyalar ve çalıştırma
sırası korunur. **İş sırası** görünümünde Slurm betiklerinin sıra numaraları görünür.
Slurm işlerinin sırasını değiştirmek için filtreyi temizleyin, **İş sırası**
görünümünü seçin ve yukarı/aşağı oklarını kullanın.

**TRUBA** dosya tarayıcısında açık klasörü ada veya uzantıya göre filtreleyin.
**Türe göre grupla** seçeneği klasörleri, betikleri ve diğer dosyaları ayırır.

Kuyruk, hesap, MPI görev sayısı, bellek ve süreyi betiğin `#SBATCH`
satırlarında belirleyin. ESEN iş takibi için iş adını, çalışma dizinini ve
standart günlük yollarını ayarlar. MPI ile derlenmiş LAMMPS'i örneğin
`mpirun -np "$SLURM_NTASKS" lmp -in 07-cekme.in` ile başlatın. İş durumu,
günlükler ve çıktılar **İş geçmişi**'nde görünür.

İş geçmişinde Slurm aşaması ve çalışma süresi gösterilir. Süreye kuyrukta
bekleme ve dosya aktarımı dahil değildir. Bir `run_*.slurm` dosyasının içindeki
aşamaları da göstermek için betiğin standart çıktısına `ESEN_STEP=` satırları
yazın. Örneğin LAMMPS çağrısından önce `echo 'ESEN_STEP=LAMMPS çekme analizi'`
ekleyin. LAMMPS girdi betiğinden adım bildirmek için
`print "ESEN_STEP=Minimizasyon" screen yes` kullanılabilir.

### TRUBA’ya yüklenmiş analizleri tek düğmeyle çalıştırma

Proje klasörünü `/arf/scratch/<kullanıcı>/RustMech/` altına yükleyin. Her analiz
klasöründe Slurm betiğini ve betiğin kullandığı dosyaları bir arada tutun.
Projenin üst klasörüne `Run.esen` adlı UTF-8 metin dosyasını koyun ve çalışacak
betiklerin göreli yollarını, her satıra bir yol gelecek şekilde yazın.

```text
cekme-300K/run.slurm
cekme-600K/run.slurm
cna/analiz.slurm
```

İlk aktarımı ESEN’den yapmak için **TRUBA Files** panelinde bir uzak proje
klasörü oluşturup açın. **Folder** ile yerel proje klasörünü seçin ve
**Send Truba** ile içeriğini açık uzak klasöre yükleyin.

**TRUBA Files** panelinde uzak proje klasörünü açtığınızda `Run.esen` seçilir.
Bir kez **Run**’a basın. ESEN listedeki her betiği TRUBA’da kendi analiz
klasörünün bir çalışma kopyasından ayrı bir Slurm işi olarak gönderir.
İş kimliklerini, durumları ve günlükleri **İş geçmişi**’nden izleyin.

Yeni bir analiz eklemek için klasörünü TRUBA’ya yükleyip betiğin yolunu listeye
ekleyin. Tek bir analizi çalıştırmak için kendi `.slurm` dosyasını seçip
**Run**’a basabilirsiniz.

### Klasörden LAMMPS girdi betiği gönderimi

**Folder** ile tek bir analiz klasörünü veya bağımsız analizlerin bulunduğu üst
klasörü seçin. ESEN klasördeki ve alt klasörlerdeki dosyaları listeler.
Çalıştıracağınız ana LAMMPS betiklerini işaretleyin.

**Send Truba** ile girdileri aktarın. Aktarım tamamlanınca **Çalıştır**, işaretli
betikleri mevcut TRUBA ayarlarıyla ayrı Slurm işleri olarak gönderir. Her işin
çalışma dizini kendi analiz klasörüdür. Veri, potansiyel ve diğer bağımlılık
dosyalarını bu klasörün içinde tutun ve betikte
göreli yollar kullanın. **Devam girdisi** ayarlandıysa aynı göreli yol her seçili
klasörde bulunmalıdır. İşler **İş geçmişi**'nde ayrı görünür ve sonuçlar her
analiz klasörünün kendi `Results` dizinine indirilir.

### TRUBA'da ara kayıttan devam ve 70. saatte devir

LAMMPS devam betiğini iş dosyalarına ekleyip göreli yolunu **Devam girdisi**
alanına yazın. Başlangıç ve devam betiklerinde aşağıdaki gibi tek dosyalı,
adım numaralı ara kayıtlar oluşturun. Aralığı, kaybedilebilecek hesaplama
süresine ve diskteki yerinize göre seçin.

```lammps
restart 10000 ${esen_checkpoint_dir}/step.*
```

Devam betiği `read_restart ${esen_checkpoint}` ile başlar. Bu komuttan sonra
gerekli kuvvet alanı, fix kimlikleri, timestep ve çıktı ayarlarını yeniden kurun.
Örneğin son hedef 50000 ise `run 50000 upto` kullanın. Böylece yalnızca
kalan adımlar hesaplanır. Her Slurm parçası için ayrı yörünge ve diğer çıktı
adlarında `${esen_segment}` kullanın. Önceki parçanın son ara kaydından
sonraki çıktı kareleri birleştirme sırasında örtüşebilir. Devam için aynı
LAMMPS derlemesi ve fiziksel parametreler kullanılır.

72 saatlik iş yaklaşık 70. saatte yeni bir Slurm işi gönderir. Yeni iş kaynak
alınca önce okunabilir bir ara kayıt doğrular, eski işi durdurur ve Slurm
temizliği bittikten sonra en yeni okunabilir kayıttan devam eder. Geçerli
ilerleme yoksa eski iş 72 saatlik sınırına kadar çalışabilir. Kuyruk bekleme
süresini Slurm belirler. RustMech kapalıyken de devir işler.

Slurm işi süre sınırında (TIMEOUT) bitirirse, Esen aynı
koşunun çalışan veya bekleyen işi olmadığını doğrular ve devam girdisi
tanımlıysa aynı TRUBA klasöründe otomatik bir devam işi gönderir. Yeni iş
en son okunabilir ara kaydı yükler. Otomatik TIMEOUT kontrolü, Esen iş takibi
başladığında ve TRUBA bağlantısı açıkken çalışır. Bir devam işi süre sınırına
kadar yeni bir ara kayıt üretemezse, Esen aynı adımı sürekli yeniden kuyruğa
almaz. İlk ara kayıt oluşmadan kesilen hesapta sürdürülecek adım yoktur.
Elle devam etmek için **İşi yenile**,
ardından **İşi sürdür** düğmesini kullanın. **İşi durdur**, o koşunun çalışan
ve bekleyen Slurm işlerini iptal eder. Ara kayıtlar TRUBA scratch alanında
saklanır.

## Ortak analiz projesi

Üst çubuktaki **Project / Proje** menüsünden **Save project** ile
`.rustmech.json` dosyası oluşturulur. Dosya aynı numunenin geometrisini,
mekanik malzemesini ve analiz ayarlarını, Flow ve Thermal (CHT) girdilerini,
bölge/malzeme referanslarını ve kaydedilmiş sonuç klasörü bağlantılarını saklar.
İlk proje modeli mevcut üç çalışma alanını kapsar; bağımsız katı termal
çözümü sonraki yol haritası adımıdır. Termal ve mekanik malzemelerin bugün
ayrı tanımlandığı bilgisi dosyada korunur; otomatik eşitlenmez.

**Open project** eski `settings.json`, `flow_case.json` ve
`thermal_case.json` dosyalarını da proje taslağına dönüştürerek açar.
Açma, analiz veya önizleme başlatmaz; geometriyi görmek için **Apply**,
çözmek için ilgili açık analiz komutu kullanılır. Bozuk dosya mevcut
çalışmayı değiştirmez. Çalışan analiz/ağ veya toplu iş sırasında proje
değiştirme engellenir. Menülü açma, kaydedilmemiş girdileri korumak için
Save/Discard/Cancel seçimi sunar.

Geometri ve sonuç dosyaları projeye gömülmez; dosya/klasör bağlantıları saklanır.
Göreli yollar proje dosyasının klasörüne göre çözülür. Pencere ve bölücü
tercihleri kullanıcıya özel mevcut kayıtlarında kalır; proje bunları değiştirmez.

İlgili kontroller: `python -m unittest tests.test_analysis_project tests.test_project_workspace`.

## Kanal ve yapı seçenekleri

Flow ve Thermal (CHT) çalışma alanlarında silindir yanında **Square / rectangular**
kesit seçilebilir. Width ve Height kesit ölçüleridir. **Insert section / mm**
sıfırken dolgu bölgesi Model küp boyunu kullanır; pozitif değer bu bölgenin
uzunluğunu belirler. Giriş/çıkış uzantıları ayrıca ayarlanır. Numune X/Y/Z
ötelemeleri ve X, sonra Y, sonra Z sırasındaki dönmeler numune merkezine göre
uygulanır. Geometri uzatılıp ölçeklenmez; sonlu numune kanal/dolgu bölgesine
kırpılır. Numune zarfının dışında boşluk varsa **Allow intentional bypass**
açıkça seçilmelidir. Bu boşluk akış ve ısı geçişini önemli ölçüde değiştirebilir.

Üretilmiş TPMS, Truss Lattice ve Porous aileleri bu iki çalışma alanında
kullanılabilir. Porous rastgeleliği Model seed'i ile korunur. İnce gözeneklerin
ağda kapanması veya kopuk akışkan cepleri yine analiz ön kontrollerine tabidir;
her ailedeki her geometri için çözüm garantisi verilmez.

Flow alanındaki **Inspect geometry (no solve)**, tıkalı akış yolunu veya izin
verilmemiş bypass boşluğunu da incelemek için kullanılır. **Geometry diagnostics**
görünümünde zarf dışındaki akışkan turuncu, iki uca bağlı olmayan bileşenler
kırmızı gösterilir; en büyük 64 kopuk bileşenin merkezleri işaretlenir. İnceleme
ayrı dosyaya kaydedilir, çözücü komutu veya OpenFOAM çalıştırma dosyaları üretmez.
TRUBA modunda aynı inceleme mevcut mesh yeteneğiyle uzakta yürür. Bu kontrol
örneklenmiş bağlantıyı gösterir; alt hücre boyutundaki duvar/gözenekleri veya
yüzeyin sürekli minimum kalınlığını kesin olarak doğrulamaz.

İnceleme ayrıca üçgenlerin kendiyle kesişmesini toleranslı geometrik testlerle
denetler. Ortak kenar/köşe tek başına hata sayılmaz. Mor üçgenler kesişen veya
dejenere yüzeyleri, sarı noktalar önerilen üç hücreden daha ince örneklenmiş
duvar/boşluk konumlarını gösterir. Sonuçlar `surface_audit.vtp` içinde saklanır
ve uzaktaki incelemelerden de indirilir. Kesişme arama bütçesi dolarsa sonuç
**incomplete** olur; yüzey doğrulanmış sayılmaz. İçe aktarılan geometrilerin
CFD/CHT ön kontrolünde tam kesişme taraması uygulanır; hatalı yüzey çözüme
gönderilmez.

Kalınlık göstergesi, seçilen üçgen merkezlerinden normale doğru ilk yüzey
vuruşunun mesafesidir. Örnek sayısı ve toplam yüz sayısı görünür; bu değerler
kesin global minimum duvar/gözenek boyutu değildir. Kanal kırpılmasından önceki
sonlu numune yüzeyi ölçülür; dışarı doğru yüzey vuruşu yoksa gözenek mesafesi
sıfır kabul edilmez. Ölçüm geometriyi veya seçilmiş ağ boyutunu değiştirmez.

**Designs > Import geometry for flow / thermal** STL/OBJ/PLY/VTP dosyasını
mm olarak alır. Kapalı, manifold yüzey gerekir; ayrı kapalı bileşenler kabul
edilir. Ölçek değiştirilmeden kapsayan küpe ortalanır. Mekanik basma yüzeyi
seçimi istenmez. Gitterburg havuzunda tek tasarım seçip **Use for flow / thermal**
ile aynı yol kullanılabilir. Bu genel numuneyi mekanik çözümde kullanmak için
mekanik import yolunda destek/yükleme yüzeyleri ayrıca hazırlanmalıdır.

Mevcut ağ basamaklı Kartezyen ağdır; yüzeye oturan sınır tabakalı ağ sonraki
yol haritası adımıdır. Analitik kare kanal ve gerçek OpenFOAM kontrolleri:
`python tools/verify_channel_pipeline.py`. 17 Kasım 2026 kontrolünde 8/16 hücrelik
kare kanal ağlarında basınç kaybı hatası %5,55/%1,47 oldu. BCC dikdörtgen ve
Gaussian Random Field silindir CHT eş sıcaklık örnekleri tamamlandı. Bunlar
numunelerin genel ağ bağımsızlığını veya deneysel performansını kanıtlamaz.
Kare kanal referansı [gelişmiş laminer akışın seri çözümüdür](https://doi.org/10.1063/1.4794500).

## Ortak SI malzeme kartları

Mekanik Material **Load**, eski mekanik JSON yanında sürüm 2 SI kartlarını
ve sıcaklık tablosu CSV dosyalarını okuyabilir. Flow/CHT'de **Load fluid material**,
CHT'de ayrıca **Load solid material** bulunur. **Use Model material**, mekanikte
yüklü kartın termal özelliklerini açık komutla kullanır; mekanik kartta k/cp
yoksa tahmini değer eklemek yerine hata gösterilir. Başlangıç örnekleri
`materials/examples/solid-si.json` ve `materials/examples/fluid-si.json` içindedir;
bunlar gerçek malzeme için doğrulanmış veri tabloları değildir.

Kart `format: rustmech-material`, `schema_version: 2`, `units: SI`, `kind:
solid/fluid`, `name` ve `properties` alanlarını taşır. Özellik adları/birimleri:
`density` kg/m³, `young_modulus`/`yield_stress`/`tangent_modulus` Pa,
`poisson_ratio` boyutsuz, `conductivity` W/(m·K), `heat_capacity` J/(kg·K),
`thermal_expansion` 1/K, `dynamic_viscosity` Pa·s. Mekanik editördeki g/cm³ ve
MPa dönüşümleri otomatik yapılır. Plastik eğride gerinim boyutsuz, gerilme Pa'dır.

Bir özellik tek sayı veya `{ "temperature_K": [293.15, 353.15], "values": [...] }`
tablosu olabilir. CSV başlıkları `temperature_K,density,conductivity,...`
biçimindedir; sıcaklıklar artan sırada olmalıdır. Tablo dışında ekstrapolasyon
yapılmaz. Mevcut sabit özellikli çözümlerde mekanik/izotermal akış için 293,15 K,
CHT akışkanı için giriş sıcaklığı, katı için başlangıç sıcaklığı kullanılır.
Kaynak kart ve tablolar proje/vaka dosyalarında korunur. Sıcaklığa bağlı
özelliklerin çözüm boyunca güncellenmesi yol haritasının 17. adımıdır.

## Arayüz dili ve TRUBA bağlantısı

Sağ üst köşedeki **English / Türkçe** seçicisinden arayüz dili değiştirilir.
Tercih kullanıcıya özel saklanır ve sonraki açılışta geri yüklenir. Dil geçişi
açık yardımcı pencereleri, menüleri, açıklamaları ve sonuç grafiklerinin
başlıklarını da günceller; sayısal girdiler, çözücü kimlikleri ve kayıtlı plan
verileri değişmez.

TRUBA penceresinde **Bağlantı**, **Kaynaklar** ve **Gelişmiş** sekmeleri
kimlik bilgilerini, kaynak oturumunu ve uzak çalışma ortamını ayrı sunar.
Pencereyi açmak veya dil değiştirmek bağlantı, kaynak tahsisi ya da analiz
başlatmaz. Bağlantı **Bağlan**, hesaplama modu ve kaynak tahsisi kendi açık
eylemleriyle başlatılır. Pencerenin konumu, boyutu ve büyütülmüş durumu korunur.

`truba_client.TrubaSession`, bağlantı ve sunucu kimliği doğrulamasını Qt'den
bağımsız yürütür. Çağıran uygulama `known_hosts` dosyasının dizinini ve durum,
parmak izi ve hata geri çağrılarını sağlar. Bağlantı açıkken `session.auth`
değerini `truba_ssh.ssh_connection` işlevine vererek aynı süreçte komut ve
SFTP aktarımı için mevcut SSH oturumunu kullanabilir. RustMech analiz isteklerinin
Slurm'a gönderimi ve sonuçların alınması `truba_remote` üzerinden yürür.

## Gitterburg Tasarim Havuzu

Gitterburg'da alt komut cubugundaki **Output / Cikti** menusunden
**Send to RustMech / RustMech'e gonder** secilir. Gercek uretim geometrisi,
tasarim adi, mm birimi ve kaynak parametreleri kullaniciya ait ortak havuza
bagimsiz bir kopya olarak kaydedilir. RustMech'in acik olmasi gerekmez.

RustMech'te **Designs > Design Pool (Gitterburg)** menusunu acin. Bir veya
birden fazla tasarim secip **Configure selected** ile destek ve yukleme
yuzeylerini belirleyin. Ana Mechanic calisma alaninda malzeme, mesh, yukleme
ve cozucuyu ayarlayin; **Designs > Add current model to analysis queue**
ile isi ekleyin. Coklu secimde her eklemeden sonra siradaki tasarim hazirlanir.
**Designs > Analysis queue** penceresinde isleri secin, yukari/asagi tasiyin
ve **Run analysis queue** ile baslatin. Gonderme, havuzu acma veya kuyruga
ekleme otomatik analiz baslatmaz. Bu kuyruk mekanik analizler icindir;
Flow/Thermal calisma alanlarinin mevcut ayri akislari korunur.

Havuz varsayilan olarak `%LOCALAPPDATA%/Gitterburg-RustMech/DesignPool`,
kuyruk ise `%LOCALAPPDATA%/Gitterburg-RustMech/RustMech/analysis_queue.json`
konumundadir. Kuyruk her isin geometri, malzeme ve analiz ayarlarini saklar;
Gitterburg'daki sonraki duzenlemeler hazir isleri degistirmez. Kesilen isler
yeniden acilista isaretsiz gelir; otomatik yeniden baslatilmaz. Pencere
boyutlari ve konumlari da kullaniciya ozel saklanir.

Analize hazirlanan geometri kapali, manifold ve tek baglantili bir kati
olmali; iki karsilikli paralel duz yukleme yuzeyi secilebilmelidir. Uygun
olmayan geometrinin nedeni gosterilir. Programlarin kaynak koddan guncel
surumlerini calistirin; mevcut eski EXE'ler bu degisikliklerle yeniden
paketlenmedikce yeni komutlari icermez.

Entegrasyon kontrolu (cozucu baslatmaz):
`..\Gitterburg\.venv\Scripts\python.exe tools\verify_design_pool_transfer.py`
Testlerde `RUSTMECH_DESIGN_POOL` ve `RUSTMECH_ANALYSIS_QUEUE` ile gecici
konumlar kullanilabilir. Uretimde havuzu degistirirken iki uygulamaya da ayni
`RUSTMECH_DESIGN_POOL` konumu verilmelidir.

## Geometri önizlemesi ve isteğe bağlı analiz ağı

DoE **New geometry > Add**, güncel yapı ve ölçüleri parametre kopyası
olarak plana ekler. Yüzey önizlemesinin tamamlanmasını beklemez; ekleme
sırasında önizleme, FE hacim ağı veya analiz işi başlatmaz. Sayısal alana
yazılan son değer Add tıklamasında onaylanır; geçersiz ölçüler eklenmez.
DoE ekleme, deney/ML tablosu oluşturma ve yüzey önizlemelerinde yalnız yapı ve
ölçüler doğrulanır. Mesh türü/stratejisi, global/yerel ağ boyutu ve eleman
sayısı bu adımları engellemez; ayarlar değiştirilmeden saklanır. Mesh
kontrolü/üretimi veya analiz istendiğinde tam FE doğrulaması uygulanır.

DoE üst çubuğundaki **Save** ve **Open**, planı `.rustmech-doe.json`
dosyasına kaydeder ve geri açar. Dosya; tasarımları ve dahil edilme seçimlerini,
grupların değişken aralıklarını, örnekleme/şekil değiştirme ayarlarını, New
Geometry taslağını, DoE ve ML tablolarını ve tablolardaki sonuç değerlerini
saklar. Geometri ve sonuç dosyalarının kendisi pakete eklenmez; mevcut dosya
yolları korunur. Open analiz veya önizleme işi başlatmaz; geri yüklenen
geometriyi görmek için **Apply** kullanılır. Kaydedilmemiş değişiklikler varsa
Open önce uyarır; geçersiz dosya mevcut planı değiştirmez. Save/Open, çalışan
toplu iş bitene veya durdurulana kadar devre dışıdır.

Ana çalışma alanı artık **Geometry** görünümüyle açılır. Geometri/Structure
**Apply** yalnız yüzeyi hazırlar; Netgen veya HEX hacim ağı üretimini beklemez.
**FE Mesh**, **Overlay**, mesh bölümünün **Apply** düğmesi veya **Run analysis**
hacim ağı üretimini açıkça ister. Run analysis önce eksik ağı arka planda
hazırlar, doğrular ve aynı ağı çözücüye aktarır. Bu sırada ayarlar değişirse
veya üretim iptal edilirse bekleyen analiz kendiliğinden başlamaz. Geometri
önizlemesinde FE eleman/düğüm göstergeleri ağ hazırlanana kadar `—` gösterir.
TRUBA modunda yüzey önizlemesi ayrı `surface` işi, FE ağı ayrı `preview` işi
olarak çalışır; bu değişiklik uzaktan hesaplama politikasını değiştirmez.

Gitterburg'un VTK Flying Edges yüzey çıkarımı RustMech'in mevcut alan
örneklemesi, katı oranı eşiği ve kapalı sınır kurallarına uyarlandı. Açık veya
manifold olmayan hızlı konturlar Lewiner Marching Cubes yoluna döner.
Geometri çözünürlüğü düşürülmez; uyumlu yüzey ve FE ağı önbellekleri korunur.
GPU altyapısı taşınmadı. Büyük referans yüzeylerde aşağıdaki otomatik parçalı
üretim yolu kullanılır.

### Otomatik parçalı yüzey üretimi

İstenen numune ölçüsü ve yüzey çözünürlüğünün gerektirdiği ızgara 16 milyon
noktayı aşınca veya tek parça alan tamponlarının tahmini o anda boş belleğin
dörtte birini aşınca **chunked mode otomatik açılır**. DoE Active/DesingLab
önizlemeleri, ana geometri önizlemesi ve referans yüzey isteyen analiz yolları
aynı politikayı kullanır. Mesh boyutu, numune ölçüsü, katı oranı eşiği ve yüzey
çözünürlüğü değiştirilmez; bunun için ayrı bir UI seçeneği gerekmez.

Alan yaklaşık iki milyon noktalık sınırlı bloklarda örneklenir; uygun periyodik
ızgaralarda kesimler hücre sınırlarına oturtulur. Aynı fazdaki iç bloklar ve
aynı dış kapak düzenine sahip bloklar bir kez konturlanıp yeniden kullanılır.
Tek hücrenin alan örnekleri de tekrar değerlendirilmez. Izgaraya tam oturmayan
periyotlarda ve rastgele gözenekli yapılarda şablon kopyalama uygulanmaz;
istenen koordinatlar sınırlı bloklarda doğrudan değerlendirilir.

Bloklar ortak örnek düzlemini paylaşır, her ızgara hücresi yalnız bir bloğa
aittir ve iç sınırlara kapak eklenmez. Birleşim noktaları aynı global ızgara
kenarının skaler değerlerinden hesaplanır. Çıkış koordinatları önceki VTK
yoluyla aynı float32 hassasiyetindedir; boyutsal yuvarlama veya yakın noktaları
toleransla kaynaştırma yapılmaz. Dış yükleme düzlemlerindeki yalnız kayan nokta
yuvarlama hataları düzeltilir. Hızlı kontur kapalı/manifold değilse aynı blok
planı Lewiner ile tekrar çıkarılır. Yeni oluşturulmuş yüzeyin daha önce yapılmış
topoloji kontrolü normal hesabından sonra gereksiz yere tekrarlanmaz.

Şablon önbelleği en fazla 128 MiB veya boş belleğin 1/16'sı kadardır. Sığmayan
ara şablonlar kullanıcıya ait geçici çalışma klasörüne yazılır. Son yüzey ve
birleştirme tamponları yine belleğe sığmalıdır; mevcut bellek ön kontrolleri
korunur. Parçalı üretim sınırsız büyüklükte son yüzey garantisi vermez.

Karşılaştırma komutu (bağımsız süreçler, üç tekrar medyanı; çizim/çözücü hariç):

```powershell
& '..\Gitterburg\.venv\Scripts\python.exe' tools/benchmark_chunked_surface.py --repeats 3 --unaligned
```

`chunked_surface_benchmark.json`; tek parça, parçalı ve periyodik şablonları
kapalı parçalı yolların süre ve tepe RSS belleğini raporlar. Hacim, kapak
alanları, sınırlar, kapalı/manifold yüzey ve her yönde en fazla 4096 köşe için
yüzeye uzaklık karşılaştırılır. Birim kontrolleri `tests.test_chunked_surface`
içindedir; ortak sınırlar, iç kapak oluşmaması, rastgele/uyumsuz periyotlar,
disk taşması, Lewiner geri dönüşü ve otomatik bellek politikası kapsanır.

16 Kasım 2026, üç tekrar medyanı (1 mm hücre, aynı yüzey çözünürlüğü):

| Geometri / numune | Tek parça | Otomatik chunked | Tek parça / chunked tepe RSS |
| --- | ---: | ---: | ---: |
| Gyroid, 4×4×4 hücre | 5,965 s | 4,767 s | 1,312 / 0,857 GiB |
| BCC, 4×4×4 hücre | 2,945 s | 3,573 s | 0,969 / 0,591 GiB |
| Gyroid, 3,13 mm, ızgaraya oturmayan periyot | 13,739 s | 9,219 s | 1,764 / 1,323 GiB |

Periyodik şablonların kullanılması aynı parçalı yöntemi Gyroid'de 1,70×,
BCC'de 1,22× hızlandırdı. Hizalanmış örneklerde 64 bloktan yalnız 27'si
konturlandı, kalan 37 blok yeniden kullanıldı; alan yalnız 80³ noktada
değerlendirildi. Hizalanmayan örnekte şablon tekrar kullanımı sıfırdır.
Chunked mode her geometride tek parça yöntemden hızlı değildir: bu BCC
örneğinde yaklaşık %21 süre maliyeti karşılığında tepe bellek yaklaşık %39
azaldı. Bellek baskısı ve büyük boyutlar otomatik seçimin ana gerekçeleridir.

Üç örnek de kapalı/manifold kaldı; sınırlar aynıydı. En büyük bağıl hacim
farkı %0,000866, en büyük bağıl kapak alanı farkı %0,000030 ve örneklenen iki
yönlü yüzey uzaklığı 4,13e-7 mm oldu. Uzaklık kontrolü her yönde en fazla 4096
köşeyi örnekler; tam Hausdorff hesabı değildir. Ölçümler çizim, süreç açılışı,
FE hacim ağı üretimi ve çözücü süresini kapsamaz. Ham rapor kökteki
`chunked_surface_benchmark.json` dosyasındadır; bu ölçüm çıktısı mevcut Git
politikasına göre ignore edilir, tekrar üretim betiği kaynakta tutulur.

Tekrar üretilebilir karşılaştırma:

```powershell
& '..\Gitterburg\.venv\Scripts\python.exe' tools/benchmark_surface_preview.py
```

`surface_preview_benchmark.json`, aynı skaler örnekler üzerinde eski/yeni
yüzey çıkarımını; kapalı/manifold yüzeyi, yönelimi, hacmi, sınırları, yükleme
yüzeyi alanlarını ve iki yönlü köşe-yüzey uzaklıklarını karşılaştırır.
Gyroid sheet/network ve BCC için 1 ve 2 hücre/eksen olmak üzere altı örnek
kullanılır. Önizleme akışı ayrıca HEX8 ve TET4 için ölçülür. Süreler sıcak
süreçte üç tekrarın medyanıdır; eski tam önizleme ölçümünde tetra ağ
önbelleği her tekrarda temizlenir. Çizim, süreç açılışı, veri aktarımı, gerçek
çözücü ve SLURM bekleme süreleri dahil değildir.

16 Kasım 2026'da testlerle eşzamanlı çalıştırılmayan son ölçüm:

| Önizleme hesabı (1 mm numune, 1 mm hücre) | Önce: yüzey + FE ağı | Şimdi: yalnız geometri | Hız oranı |
| --- | ---: | ---: | ---: |
| HEX8, 0,1 mm ağ | 90,96 ms | 86,55 ms | 1,05× |
| TET4, Netgen, 0,1 mm global/yerel ağ | 4,100 s | 87,02 ms | 47,11× |

Yalnız yüzey çıkarımı altı örnekte 1,04–1,34× hızlandı. Altı örneğin
tamamında Flying Edges kullanıldı ve doğruluk eşikleri geçti: açık kenar yok,
kapalı/manifold yüzey, aynı sınırlar, en büyük bağıl hacim farkı %0,0003952,
en büyük iki yönlü köşe-yüzey uzaklığı 1,165e-7 mm. TET4 önizlemesindeki büyük
kazanç hacim ağının ertelenmesidir; analiz toplam süresinin 47× hızlandığı
anlamına gelmez. Her iki FE karşılaştırma ağı da mevcut çözüm ön kontrollerini
geçti; harici çözücü çalıştırılmadı.

Regresyon testleri `tests.test_surface_preview` ve
`tests.test_surface_preview_ui` içindedir. 16 Kasım 2026 doğrulamasında ilgili
73 çekirdek/Qt testi ve 28 masaüstü testi geçti; pencere durumu, Enter davranışı,
önbellek, iptal, analiz ağı yeniden kullanımı ve sahte TRUBA akışları kapsandı.
Masaüstü testlerinde VTK OpenGL bağlam uyarıları görüldü; bu sonuç çizimin
görsel olarak doğrulandığı veya çizim hızının ölçüldüğü anlamına gelmez.
Canlı TRUBA ve gerçek çözücü çalıştırılmadı. Kaynak sürümü
`Run-RustMech.ps1` ile açılır; mevcut EXE ayrıca yeniden paketlenmelidir.

## Güncel durum ve çevrimdışı testler

### Büyük sonlu eleman modelleri

DoE ve ortak mesh doğrulamasındaki 150 bölme / 1.100.000 eleman üst
sınırları kaldırıldı. Rust HEX8/HEX20 ve TET10 çekirdeği, Netgen ve APDL
aktarımı da eski eleman sınırlarını uygulamaz. Numune boyutu, global/yerel
mesh boyutu ve geometri örnekleme aralığı otomatik değiştirilmez.
Standart numunelerin HEX elemanları da Rust çekirdeğinde oluşturulur.

Mesh üretimindeki büyük tamponlar, o anda kullanılabilir belleğe göre
ön kontrolden geçer; yetersizse gerekli ve kullanılabilir GiB bilgisi
gösterilir. Bu tahmin çözücünün matris/faktörizasyon belleğini kapsamaz.
Yerel mesh üretimi yerel belleği, uzak mesh üretimi çalıştığı sunucunun
belleğini kullanır. Yerel indekste taşmayı önleyen veri biçimi kontrolleri
korunur. Mesh Convergence eleman bütçesi varsayılan olarak `Unlimited`;
kullanıcının belirlediği pozitif bütçe uygulanmaya devam eder.

Kaynak sürümü `Run-RustMech.ps1` ile yeniden açın. Rust çekirdeği ABI 5,
özel Netgen çekirdeği `96e5682f-rustmech-atlas-6` olarak yeniden derlendi.
Eski EXE paketleri ayrıca yeniden paketlenmedikçe bu değişiklikleri içermez.

Regresyon kontrolü: `..\Gitterburg\.venv\Scripts\python.exe -m unittest tests.test_large_mesh`.
Bu test 2 milyonun üzerinde HEX elemanı üretip APDL dosyasına aktarır;
TET10 düğüm oluşturma ve kalite hesaplarını 2.000.001 elemanla sınar.
Harici çözücü başlatmaz.

RustMech; PySide6/VTK arayüzü, Rust/Netgen ağ çekirdekleri, yerel ANSYS MAPDL,
TRUBA/Kratos mekanik analizi ve ayrı OpenFOAM CFD/CHT çalışma alanlarını içerir.
Mekanik `openfoam_backend.py` adaptörü **uygulanmamış ve kapalıdır**;
`write_openfoam_case_skeleton` bilinçli olarak `NotImplementedError` üretir.
Bu durum çalışan Flow/Thermal yollarının eksik olduğu anlamına gelmez.

Aşağıdaki hızlandırma turları ve test sayıları geçmiş çalışmaların kayıtlarıdır.
Bu bölümlerdeki “EXE paketlenmedi”, “ABI 2” ve “doğrulandı” ifadeleri ilgili turun
durumunu anlatır; bugünkü kaynak/EXE eşdeğerliğini veya bu denetimde yeniden test
edildiğini göstermez. Dağıtımın güncelliği ayrıca sınanmalıdır. Son denetimin
bulguları, çalıştırma engelleri ve öncelikleri [IMPROVEMENT_REPORT.md](IMPROVEMENT_REPORT.md)
içindedir.

Başlatıcı önce proje `.venv` ortamını, sonra `../Gitterburg/.venv` ortamını,
son olarak PATH üzerindeki `python` komutunu seçer. Testler de aynı Python ile
çalıştırılmalıdır. PowerShell örneği (proje kökünden):

```powershell
$testPython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $testPython)) {
    $testPython = '..\Gitterburg\.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $testPython)) {
    $testPython = (Get-Command python -ErrorAction Stop).Source
}
$previousOffline = $env:RUSTMECH_OFFLINE
try {
    $env:RUSTMECH_OFFLINE = '1'
    & $testPython -m unittest discover -s tests -v
    $testSucceeded = $?
    $testExitCode = $LASTEXITCODE
    if (-not $testSucceeded) {
        throw "Test komutu başarısız veya başlatılamadı (exit: $testExitCode)."
    }
} finally {
    $env:RUSTMECH_OFFLINE = $previousOffline
}
```

`RUSTMECH_OFFLINE=1`, gerçek yerel SSH soketi açan
`test_real_local_ssh_password_handshake` testini atlar. Sahte SSH/SLURM ile
çalışan birim testleri korunur; bu değişken işletim sistemi düzeyinde ağ engeli
değildir. `tools/verify_truba_*.py` canlı hesap/bağlantı kullanabilir ve bu
komutun parçası değildir. Gerçek MAPDL, WSL/OpenFOAM ve Linux işçi doğrulamaları
ayrı entegrasyon kontrolleridir. Qt testleri, yerel DLL'ler ve bazı testler için
Git Bash gerekir; eksik isteğe bağlı bileşenlerin skip gerekçelerini sonuçta okuyun.

Mekanik ayarlar menüsünde **Finite Element** ve **FFT Homogenization** çalışır.
Kırpılmış voxel ağı, Material Point Method ve Peridynamics plan durumundadır.
Teslim sırası ve doğrulama ölçütleri
[mekanik yöntemlerin uygulama planında](MEKANIK_YONTEM_PLANI.md) yer alır.

### DAMASK ile FFT homojenizasyonu

Windows'ta DAMASK grid çözücüsünü ve `python3-h5py` paketini WSL
dağıtımına kurun. Ubuntu 26.04 için
[DAMASK'ın paket kurulumu](https://damask-multiphysics.org/installation/package_manager.html)
izlenebilir. İlk sürüm DAMASK 3.1.0 ile doğrulandı.

1. Mekanik ayarlarda periyodik bir TPMS veya lattice yapısı seçin.
2. Yöntem menüsünde **FFT Homojenleştirme** sayfasını açın. Izgarayı ve boşluk/katı
   rijitlik oranını girin. Yerel Windows çalıştırması için WSL dağıtımını seçin. Ayarların altındaki
   **Katı faz malzemesi** panelinden malzemeyi seçin veya Young modülü ile Poisson
   oranını düzenleyin. Yerelde çalıştırmak için **Analizi çalıştır** düğmesine
   basın. TRUBA'da çalıştırmak için TRUBA hesaplama modunu açın, CPU kaynak
   oturumunun hazır olmasını bekleyin ve aynı düğmeyle işi gönderin.
3. Tamamlanan koşuda etkin 6×6 rijitlik matrisi MPa cinsinden, örneklenen katı
   hacim oranıyla birlikte gösterilir. **FFT sonuçlarını aç** düğmesi
   `results/FFT_DAMASK_*/` klasörünü açar. Klasörde CSV, girdi özeti, DAMASK
   çıktıları ve her yüklemenin yakınsama kaydı bulunur.

TRUBA çalışma ortamı DAMASK'ı bulamazsa FFT sekmesi açıkken TRUBA panelinde
**Prepare remote runtime** kullanın. Uygulamaya ait özel ortam, DAMASK Grid'i
[conda-forge](https://github.com/conda-forge/damask-feedstock) üzerinden kurar.
İş, TRUBA iş listesinden izlenir ve sonuç matrisi tamamlandığında otomatik
indirilir. Ham HDF5 çıktıları ile geometri TRUBA'daki çözücü klasöründe kalır.

Çözücü altı bağımsız yükleme yönünün her birinde pozitif ve negatif küçük
pertürbasyon uygular. Matris sırası `xx, yy, zz, yz, xz, xy` ve kayma
girdileri mühendislik kayma şekil değiştirmesidir. Boşluk, seçilen oranla
ölçeklenen yumuşak bir elastik fazdır. Raporlamadan önce sonucun ızgara
yoğunluğuna ve boşluk rijitliğine duyarlılığını değerlendirin. Yakınsamayan
veya iptal edilen koşulda etkin rijitlik CSV'si yayımlanmaz.

## Windows'ta yerel Kratos

Mekanik çalışma alanında **Yerel** hesaplama hedefini ve **Kratos Multiphysics**
çözücüsünü seçin. İlk kullanımda Kratos ayarlarındaki **Yerel Kratos'u Kur**
düğmesi, 64 bit CPython 3.14 ve internet bağlantısıyla Kratos 10.4.4'ü
`%LOCALAPPDATA%\RustMech\Kratos\10.4.4` altında yalıtılmış bir ortama kurar.
Kurulum paketleri doğrular ve ayrıntıları aynı klasördeki `install.log`
dosyasına yazar. Kaynak kopyadan aynı kurulum `python -m local_kratos` ile
başlatılabilir.

Kurulum tamamlanınca **Analizi Çalıştır**, mevcut sonlu eleman ağını bu
bilgisayardaki Kratos'a iletir. Çözümün günlüğü ham sonuç klasöründe
`kratos.log` olarak kaydedilir. Yerel analiz için TRUBA hesabı veya ANSYS
lisansı gerekmez.

## İsteğe bağlı TRUBA hesaplama modu

TRUBA panelindeki **Run mesh generation, convergence and analyses on TRUBA**
seçeneği, ağır sayısal işleri SLURM hesaplama düğümlerine yönlendirir. Bilgisayar
yalnız ayarları, iş durumunu ve indirilen VTK/CSV verilerinin yerel görüntüsünü
yönetir. Bu seçenek **her uygulama açılışında kapalıdır**. Paneli açmak veya
SSH bağlantısını kurmak hesaplama modunu ya da bir analizi kendiliğinden başlatmaz.
Mesh Convergence ekranındaki **Run on TRUBA** kutusu aynı hesaplama modunu
değiştirir. TRUBA'da çalıştırmak için kaynak oturumunun hazır olması gerekir.
TRUBA için çözücü değişirse yakınsama çalışması ana ekrandaki o çözücünün
profilini kullanır ve başlangıçtaki yükleme hedefi ile adım ayarlarını korur.

1. `.\Run-RustMech.ps1` ile kaynak sürümünü açın. **TRUBA** düğmesinden kendi
   hesabınızla Connect yapın; mevcut kayıtlı WinSCP TRUBA oturumu da kullanılabilir.
2. Uzak çalışma seçeneklerinde kuyruk, hesap, süre, bellek ve CPU sayısını girin.
   Python modülü, ayrı çalışma ortamının Python yolu ve OpenCFD modülü kullanıcıya
   özel saklanır. Varsayılan OpenCFD modül adı geçmiş kurulumla uyumludur;
   gerçekten yüklenebildiği ayrıca doğrulanır.
3. **Allocate resources** ile kaynak ayırın veya **Refresh session** ile kayıtlı
   oturumu doğrulayın. Oturum RUNNING ve işçi hazır olunca bir **SLURM yetenek kontrolü** çalışır: Linux
   Python/Rust çekirdeği, Netgen, Kratos uygulamaları ve uyumlu OpenCFD komutları
   hesaplama düğümünde kontrol edilir. Kuyruk beklerken işlemler hazır sayılmaz.
4. Çalışma ortamı eksik veya eskiyse sistem aynı tahsis içinde otomatik hazırlar.
   **Prepare remote runtime** elle onarım için kullanılabilir. Bu işlem kullanıcıya ait
   `~/.local/share/RustMech/remote-v1` ortamını, bilimsel Python bağımlılıklarını,
   Kratos 10.4.4 paketlerini ve Linux Rust çekirdeğini kurar. Sistem Python'unu
   veya kabuk açılış dosyalarını değiştirmez. Paket indirmek için hesaplama
   düğümünün internet/depo erişimi gerekir; kurulum günlükleri iş klasöründedir.
   Özel mevcut ortamlar değişiklik yapılmadan kontrol edilebilir; otomatik kurulum
   yalnız RustMech'in kullanıcıya özel `remote-*` ortamlarını destekler.
   Küme Python'unda `_ctypes` gibi gerekli standart bileşenler eksikse kurulum,
   aynı kullanıcı ortamında [uv ile yönetilen tam Python 3.12](https://docs.astral.sh/uv/guides/install-python/)
   kullanır. Paylaşılan Python kurulumu ve kabuk başlangıç dosyaları değişmez.
5. Yetenek kontrolü başarılı olduktan sonra normal mesh/analiz düğmelerini kullanın.
   Bağlantı veya çalışma ortamı değişirse otomatik kontrol yeniden yapılır.
   İlk kurulum indirme ve derleme nedeniyle birkaç dakika veya daha uzun sürebilir;
   aşaması üst başlıkta ve Advanced durum alanında gösterilir. Hazır ortam yeniden
   kurulmaz. Kontrol ve kurulum işleri kendi Stop düğmesiyle iptal edilebilir.

TRUBA modunda mevcut destek:

| İşlem | Yürütme |
| --- | --- |
| Model ve DoE sınır geometrisi | Geometri ve FE ağı TRUBA'da; yüzey/mesh yerelde çizilir |
| Mekanik analiz | Doğrulanmış Kratos adaptörü; önizlemedeki aynı uzak FE ağı yeniden kullanılır |
| Mesh convergence | Her seviyenin mesh, çözüm ve metrikleri TRUBA'da; sıradaki seviyeyi seçen hafif arayüz mantığı istemcide |
| DoE mesh kontrolü / analiz | Satırın mesh ve çözüm işlemleri TRUBA'da |
| Flow (CFD) | Akış ağı, OpenFOAM çözümü ve sonuç hazırlama TRUBA'da |
| Thermal (CHT) | Su/katı ağı, zamana bağlı CHT ve 3B karelerin hazırlanması TRUBA'da |
| İçe aktarılan STL/VTK parçası | Hizalanmış yüzey yüklenir. TET4/TET10 ağ ve desteklenen Kratos basma çözümü TRUBA'da çalışır. |
| Standart deney geometrileri | Basma, çekme ve eğilme geometrisi/ağı TRUBA'da; mevcut çekme/eğilme çözümleri ANSYS'e bağlı olduğu için kapalı |

İçe aktarılan parçanın önizlemesi ve çözümü ayrı iş klasörlerinde çalışabilir.
Aynı FE ağı yeniden kullanılırken geometri dosyasının SHA-256 değeri ve bütün
mesh ayarları doğrulanır. Parça değişmişse veya eski önizlemede doğrulama bilgisi
yoksa yeniden mesh istenir. Geri alınan önizleme yalnız mevcut geometri ve mesh
ayarlarıyla eşleşiyorsa çalışma alanına uygulanır.

Uzak işlem listesi istemci ve hesaplama düğümünde ortak olarak denetlenir.
Bilinmeyen işlemler ve ANSYS çözüm istekleri yeni gönderimde bağlantı kurulmadan,
hesaplama düğümünde ise geometri/ağ üretilmeden reddedilir. Bu sınır ANSYS'in
yerel kullanımını değiştirmez. Kratos'un desteklemediği eleman/temas ayarları da
hesaplama düğümünde ağ üretiminden önce kontrol edilir.

Bu akışın kullandığı [Kratos BSD lisanslıdır](https://github.com/KratosMultiphysics/Kratos/blob/master/kratos/license.txt),
[Netgen LGPL 2.1 lisanslıdır](https://github.com/NGSolve/netgen/blob/master/LICENSE)
ve [OpenCFD OpenFOAM GPL lisanslıdır](https://www.openfoam.com/news/main-news/openfoam-v2606).
Bu açık kaynak bileşenleri için ücretli bir ANSYS lisansı kullanılmaz. TRUBA hesabının
kuyruk/kurulum izinleri ayrıca gereklidir; lisans uygunluğu bir çözüm adaptörünün
uygulandığı veya doğrulandığı anlamına gelmez.

Yalnız **hem mevcut hem uygulanmış** adaptörler aktif olur. Kratos mekanik
analizi için kullanılabilirliği doğrulanır; Netgen yoksa veya ayrı süreçteki
küçük TPMS tetra ağ kontrolü başarısızsa TET4/TET10 işlemleri engellenir.
OpenFOAM CFD/CHT ayrı çalışma alanlarındadır; mekanik sekmedeki
uygulanmamış OpenFOAM solid adaptörü aktif edilmez. Uzak MAPDL/lisans akışı
henüz nitelendirilmediğinden ANSYS MAPDL, TRUBA modunda kapalıdır. Kurulu olmak
tek başına desteklenmek anlamına gelmez. Mekanik CPU sayısı uzak tahsisi aşamaz.
Kratos OpenMP kullanır; ilk genel uzak OpenFOAM akışı tek süreçlidir, çok düğümlü
MPI dağıtımı eklenmemiştir.

Ana çalışma alanındaki otomatik Kratos CPU sayısı uzak tahsisle sınırlandırılır;
elle girilen sayı tahsisi aşarsa analiz başlamaz. Uzak ortamın mesh kontrolünden
geçen HEX8, HEX20, TET4 ve TET10 ağları Kratos ile çözülebilir. HEX20/TET10 basmada
yer değiştirme kontrolü, ceza mortar ve kapalı öz temas kullanılır. Katı elemanlar
ikinci derecede kalır; plaka teması, köşe ve kenar düğümlerini kullanan doğrusal üçgenlerle
yaklaştırılır. Üç nokta eğme ve öz temas için HEX8 gerekir.

Uzak mod açıkken başarısız bir kontrol veya bağlantı nedeniyle **yerel mesh ya da
çözüm başlatılmaz**. Mod kapatıldığında önceki normal çözücü seçimi geri yüklenir;
mevcut yerel/per-solver davranışları korunur. Devam eden fizik analizinde mod
değiştirmek için işin bitmesi veya durdurulması gerekir. Uzak iptal doğrulanamazsa
iş tutamacı tekrar denemek için tutulur; çalışma kapatılmış gibi gösterilmez.

Her iş, ayarlar ve kimliği doğrulanan kaynak paketinden oluşur. Windows DLL'leri,
parolalar, settings.json ve önceki sonuçlar kaynak paketine girmez. Ağ/çözüm
üretilmesi dahil ağır işler giriş düğümünde çalıştırılmaz. Kaynak SHA-256,
SLURM iş kimliği, istek kimliği ve indirilen arşivin SHA-256 değeri doğrulanır.
Kratos'un ağ kimliği ve self-contact doğrulamaları korunur. Arşivden dizin dışına
çıkan yollar ve bağlantılar reddedilir.

Uzak ham OpenFOAM zaman klasörleri sunucuda kalır. Yerel görüntü için VTK
alanları, termal kareler, CSV ve raporlar indirilir; ilk sürüm seçili zaman adımını
isteğe bağlı indirmek yerine hazırlanmış kareleri birlikte indirir. Büyük
modellerde yerel görüntüleme hâlâ ekran kartı/bellek kullanır. İş kaynakları ve
sonuçlar `results/TRUBA_*` ile kullanıcıya özel TRUBA iş kayıtlarında izlenir.
Bağlantı kesilirse **Retrieve remote job…** aynı kaydı geri alır; kabul edilmiş
işi yeniden göndermez. Eski Kratos iş kayıtları da mevcut geri alma yolunu kullanır.

SSH kuyruk sorguları ve dosya aktarımları doğrulanmış oturumu yeniden kullanır.
TRUBA modundaki uzak iş koordinasyonu, panelin oturumunu paylaşabilmek için
istemcide bir iş parçacığında yürür; mesh ve çözücü hesaplaması yine SLURM
düğümündedir. Her sorguda yeniden parola doğrulaması yapılmaz. Kopan bir SSH
işlemi otomatik tekrarlanmaz; kabul edilmiş işin kaydıyla geri alma kullanılır.

Doğrulama:

```powershell
python -m unittest discover -s tests -p 'test_truba_remote.py'
python -m unittest discover -s tests -p 'test_remote_mode_ui.py'
python -m unittest discover -s tests -p 'test_remote_operations.py'
python -m unittest discover -s tests -p 'test_geometry_import.py'
```

15 Kasım 2026'da gerçek TRUBA/SLURM hesabında kurulum ve ortam kontrolünün
ardından 12 hesaplama senaryosu geçti: yüzey, HEX8/TET4/TET10 mesh önizlemesi,
içe aktarılan parça ve aynı ağla Kratos çözümü, TPMS mekanik çözümü, yakınsama,
DoE mesh kontrolü/çözümü, CFD ve geçici CHT. Bu 14 iş `orfoz239` düğümünde
`COMPLETED|0:0` ile tamamlandı. Kayıtlar ve sonuçlar
`build/truba-live-20261115-repaired/verification.json` içindedir.

1 mm küpün 0,001 mm basılmasında E=1000 MPa için beklenen 1 N kuvvete karşı
0,996563 N elde edildi; en büyük kuvvet dengesizliği %0,522541 oldu.
Bu küçük referans testleri genel mesh/yükleme bağımsızlığı garantisi değildir.

Arka plan işinin kayıtlı aktif SSH oturumunu ikinci bir giriş yapmadan kullanması
ayrıca gerçek SLURM işiyle `hamsi38` üzerinde doğrulandı (`6987363`,
`COMPLETED|0:0`, ek doğrulama sayısı 0). Kayıt:
`build/truba-live-20261115-repaired/shared_session.json`.

Test aracı `tools/verify_truba_operations.py` Windows kullanıcı şifrelemesiyle
saklanan RustMech girişini kullanır; parola komut satırına veya rapora yazılmaz.
Aynı `--directory` ile mevcut işleri geri alır. Değişen kaynakları veya girdileri
yeniden doğrulamak için yeni bir dizin kullanın. `--prepare-runtime` kurulumdan
sonra yeni bir ortam kontrolü yapar. İlk Python ortamındaki eksik `_ctypes`
tespit edilip kullanıcıya özel tam Python ile onarıldı; ilk denemelerin kayıtları
`build/truba-live-20261115` altında korunur.

İzole Linux ortamındaki kontroller de korunur.
Yerel Linux kontrol araçları `tools/verify_remote_linux_setup.sh` ve
`tools/verify_remote_worker_linux.sh` içindedir; bunlar TRUBA'ya bağlanmaz.
Kaynak EXE paketlemesine uzak işçi kaynakları ve ortak işlem denetimi de dahildir.

Bağlantı/iş koordinasyonu güncellemesinden sonra 90 ilgili Python testi ve
yeniden paketlenen EXE'nin açılış/mesh/bölücü kontrolü geçti. Önceki 10 Linux
Python testi ve 9 Rust çekirdek testi de başarılıdır. Linux işçisinin sekiz
referans senaryosu `build/remote-worker-linux-fdcd9559/verification.json`
dosyasında kayıtlıdır.

## Zamana bağlı su–TPMS sıcaklık analizi

**Thermal (CHT)** çalışma alanı, OpenCFD OpenFOAM 2606'nın gerçek
`chtMultiRegionFoam` çözücüsüyle suyun momentum/enerji denklemlerini ve TPMS
katısının ısı iletimini birlikte, fiziksel zamanda çözer. Suyun sıcaklığı
katıya sabit bir taşınım katsayısı verilerek tahmin edilmez; su–katı sınırında
sıcaklık ve ısı akısı eşleştirilir. Mevcut izotermal **Flow (CFD)** alanı
bağımsız olarak korunur.

Güncel sürümü `.\Run-RustMech.ps1` veya `dist/RustMech/RustMech.exe` ile açın.

1. Model alanında TPMS türünü ve geometrisini seçin; **Thermal (CHT)** alanına
   geçin. Silindirik kanal, giriş hızı ve su özellikleri burada tanımlanır.
2. **Inlet water** sıcak giren suyu; **Initial water** kanalın başlangıçtaki
   suyunu; **Initial solid** TPMS'nin başlangıç sıcaklığını belirler. Örneğin
   80 / 20 / 20 °C, başlangıçta soğuk suyla dolu kanala sıcak su girişidir.
   Sıcak cephenin TPMS'ye ulaşması giriş uzantısının uzunluğuna ve hıza bağlıdır.
3. Suyun özgül ısısını ve ısı iletkenliğini; TPMS'nin yoğunluğunu, özgül ısısını
   ve ısı iletkenliğini girin. Bunlar mekanik malzeme sekmesinden otomatik
   alınmaz. 7850 kg/m³, 500 J/(kg·K), 16 W/(m·K) varsayılanları çelik benzeri
   bir örnektir; gerçek malzemenin verileriyle değiştirilmelidir.
4. **Duration** toplam fiziksel süre, **Max time step** en büyük çözüm zaman
   adımı, **Save interval** kaydedilen 3B kareler arasındaki fiziksel süredir.
   Çözücü Courant/difüzyon sınırlarına göre zaman adımını küçültebilir.
   **Coupling correctors**, her adımda su ve katı arasındaki çözüm alışverişi
   sayısıdır; yüksek arayüz hatasında artırılabilir.
5. **Build thermal regions / export case** yalnız ağı ve başlangıç
   sıcaklıklarını gösterir. **Run transient heat transfer** açıkça basılınca
   analiz başlar. Enter bir değeri onaylar; analiz veya TRUBA bağlantısı başlatmaz.
6. Sonuçta zaman kaydırıcısı veya **Play** ile suyun ve TPMS'nin ayrı/birlikte
   sıcaklık haritalarını izleyin. Sabit renk skalası bütün zamanlarda aynıdır.
   Grafikte suyun hacim ortalaması, çıkış suyunun debi ağırlıklı sıcaklığı,
   TPMS'nin hacim ortalaması ve minimum–maksimum aralığı bulunur. Su hızı ve
   gösterge basıncı da seçili fiziksel zamanda görüntülenebilir.

İlk model **laminer, tek fazlı ve sabit malzeme özelliklidir**. Yerçekimi
sıfırdır; kaldırma kuvveti, kaynama, radyasyon, dış ortama ısı kaybı ve boru
duvarının ısıl kütlesi modellenmez. Dış kanal duvarları yalıtılmıştır, su–katı
teması idealdir. Sıcaklık, suyun yoğunluğunu/viskozitesini değiştirmez; su
özelliklerini deney koşullarına uygun girin. Yaklaşık atmosfer basıncında bu
tek fazlı model için sıcaklık girdileri 0–95 °C ile sınırlandırılır. Başlangıç
su sıcaklığı bütün akışkan hacmine uygulanır; giriş sıcaklığı ayrı bir sınır
koşuludur. Hız başlangıçta giriş hızı büyüklüğündedir, ardından zamana bağlı
basınç/hız çözümü gelişir.

Su ve katı, aynı basamaklı Kartezyen ağın iki fazıdır. `splitMeshRegions
-cellZonesOnly` kopuk katı parçalarını da aynı katı bölgesinde korur.
Tamamen izole su boşlukları bu ilk çok bölgeli sürümde desteklenmez; algılanırsa
geometri/ağ değişikliği istenir. Her iki bölge `checkMesh` kontrolünden geçer.
Örneklenen gerçek katı oranı ve kopuk katı parçası sayısı sonuçta gösterilir.
İnce TPMS duvarları için ağ bağımsızlığı ayrıca değerlendirilmelidir.

`results/Thermal_*` klasörleri vaka, günlükler, ham OpenFOAM zaman klasörleri,
`frames/` altındaki su/katı VTU kareleri, `thermal_summary.json` ve
`temperature_history.csv` içerir. Vaka `thermal_case.json` seçilerek yeniden
açılır. Arayüzde son fiziksel zaman gösterilir; Play yeniden başlangıçtan oynatır.
En fazla 200 çıktı aralığı kaydedilir. Analiz arayüzü kilitlemeden ayrı süreçte
çalışır; Stop ve pencereyi kapatma, analize ait Linux süreç grubunu da durdurur.
Ana pencere ve her iki termal bölücünün düzeni mevcut kullanıcı tercih
sistemiyle, termal girdiler ayrı kullanıcı QSettings anahtarlarıyla korunur.

Arayüz sıcaklık sıçraması ve iki taraftan hesaplanan arayüz ısı akısı farkını
raporlar. Kaydedilen adımlarda arayüz ısı dengesizliği %1'i, arayüz sıcaklık
sıçraması 0,1 K'yi veya su debisi dengesizliği %1'i aşarsa sonuç
**COMPLETED_WITH_WARNINGS** olarak işaretlenir. **COMPLETED**, istenen sürenin
temiz tamamlandığını belirtir; ağ/zaman adımı bağımsızlığı veya deneysel
doğruluk garantisi değildir. Çıkış dosyalarının okunmasında küçük basınç ve
sıcaklık farkları için OpenFOAM'ın ASCII skaler hassasiyeti korunur.

Gerçek çözücü ve arayüz doğrulaması:

```powershell
python -m unittest discover -s tests -p 'test_thermal*.py'
python tools/verify_thermal_pipeline.py
python tools/verify_thermal_ui.py
python tools/verify_flow_cancel.py --thermal
```

Kısa doğrulama modelinde 40 °C eş sıcaklık kontrolü yaklaşık 0,000007 °C
içinde korundu. 80 °C su ve başlangıçta 20 °C katı/su ile iki bölge de ısındı;
çıkış sıcaklığı başlangıç ve giriş arasında kaldı. Zaman adımını 0,01'den
0,005 s'ye düşürmek, 0,5 s'deki katı sıcaklık artışını yaklaşık %0,009 değiştirdi.
Arayüz sıcaklık sürekliliği ve ısı akısı dengesi, gerçek süreç iptali, 3B kareler
ve fiziksel zaman oynatması doğrulandı. Bu küçük doğrulama ağı, gerçek bir
TPMS numunesinin ısıl performansına ait ağdan bağımsız bir sonuç değildir.
Ekran görüntüsü: `design/transient-thermal/temperatures.png`.

## Silindirik kanal içinde TPMS akış analizi

Kaynak uygulamada **Flow (CFD)** çalışma alanı eklendi. Güncel sürümü
`.\Run-RustMech.ps1` ile açın; mevcut dağıtım EXE'si bu değişiklik için
yeniden paketlenmedi. Bu alan, mekanik Analysis sekmesindeki pasif OpenFOAM
yer tutucusundan bağımsızdır ve gerçek OpenFOAM `simpleFoam` çözümü çalıştırır.

1. **3D Model** alanında TPMS türünü, morfolojiyi, numune boyunu, hücre
   periyodunu ve katı oranını seçin. Mekanik FE ağı CFD için kullanılmaz.
2. **Flow (CFD)** alanında kanal çapını, giriş/çıkış uzantılarını, giriş
   hızını, su yoğunluğu ve dinamik viskozitesini belirleyin. Varsayılan
   yoğunluk/viskozite yaklaşık 20 °C su içindir; sıcaklığa bağlı çözüm yoktur.
3. **Build channel / export case** yalnız akış ağını ve çözüm dosyalarını
   üretir. **Run flow analysis** açıkça basıldığında ağı üretip çözücüyü
   çalıştırır. Enter yalnız alan değerini onaylar.
4. Hız ve basınç kesitleri, hacim kesitleri ve akım çizgileri görüntülenir.
   Kanalın toplam statik basınç farkı ile numunenin hemen öncesi/sonrası
   arasındaki fark ayrı raporlanır. İkinci değer giriş/çıkış etkilerini içerir;
   yalnız gözenek içi sürtünme kaybı veya toplam basınç kaybı değildir.
5. **Empty pipe reference**, aynı kanalın numunesiz karşılaştırmasını sağlar.
   Kaydedilen vaka `flow_case.json` seçilerek yeniden açılabilir. Yeni analiz
   her zaman Model alanındaki güncel geometriyi kullanır.

TPMS, küpün merkezindeki silindire kırpılır; numune uzunluğu küp boyudur,
akış +Z yönündedir. Çap küp boyunu aşamaz, böylece numune çevresinde istemsiz
yan kaçış boşluğu oluşturulmaz. Giriş düzgün hız, çıkış sıfır gösterge basıncı,
katı ve kanal duvarları kaymama koşuludur. Bütün çözücü değerleri SI'ya
dönüştürülür; OpenFOAM'ın kinematik `p` alanı yoğunlukla çarpılarak Pa gösterilir.

Çalıştırma için **WSL Ubuntu içindeki OpenCFD OpenFOAM** kullanılır. Bu
bilgisayarda doğrulanan sürüm 2606, ortam dosyası
`/usr/lib/openfoam/openfoam2606/etc/bashrc`'dir. Dağıtım ve ortam yolu panelden
değiştirilebilir. Foundation'ın farklı çözücü mimarileri desteklenmez;
`simpleFoam` ve `checkMesh` çalıştırma öncesinde kontrol edilir. Durdurma işlemi
yalnız bu analize ait Linux süreç grubunu ve Windows analiz işçisini sonlandırır.

İlk sürüm izotermal, sürekli, sıkıştırılamaz Newtonyen akıştır. Laminer model
ve isteğe bağlı k–ω SST bulunur. SST giriş türbülans şiddeti %5, uzunluk ölçeği
çapın %7'sidir; duvar fonksiyonları kullanılır. Bu varsayımlar ve duvar
yakınındaki çözünürlük ayrıca değerlendirilmelidir. Çap üzerinden Reynolds
sayısı bilgi amaçlıdır; TPMS gözeneklerindeki akış rejimini tek başına belirlemez.

Akış ağı **basamaklı Kartezyen hacimlerden** oluşur; silindir ve ince TPMS
duvarları yaklaşık temsil edilir, yüzeye oturan sınır tabakası ağı yoktur.
Katı sınırı mevcut TPMS eşik değerinden alınır, CFD ağına göre tekrar
ayarlanmaz. Silindire kırpma ve ayrıklaştırma nedeniyle gerçek katı oranı
küp hedefinden farklı olabilir; örneklenen oran sonuçta gösterilir. İnce duvar
ve gözenekler için ağ inceltme karşılaştırması zorunludur. Yalnız tamamen
izole boşluklar dışlanır; ana akışa bağlı kör gözenekler korunur. Geçişli yol
yoksa çözüm başlatılmaz. `checkMesh` başarısızsa çözücü çalıştırılmaz.

Sonuçlar `results/Flow_*` altında vaka, ağ, günlükler, `flow_result.vtu`,
`flow_summary.json` ve `axial_profile.csv` olarak saklanır. Sonuç durumu için
SIMPLE artık kriterleri ve %1'den küçük debi dengesizliği birlikte aranır;
sağlanmazsa sonuç **NOT_CONVERGED** olarak gösterilir. Pencere boyutu,
konumu, büyütülmüş durumu ve akış bölücüsü mevcut kullanıcı tercih sistemiyle,
akış girdileri de kullanıcıya özel QSettings ile korunur.

Doğrulama araçları (uygulamanın Python ortamında):

```powershell
python -m unittest discover -s tests -p 'test_flow*.py'
python tools/verify_flow_pipeline.py
python tools/verify_flow_ui.py
python tools/verify_flow_cancel.py
```

Gerçek OpenFOAM doğrulamasında boş borunun gelişmiş laminer bölümündeki
Poiseuille basınç gradyanı hatası 12/24 hücre/çap ağlarında %2,66 / %2,23
oldu. İnce ağda parabolik hız profili, TPMS'nin ek basınç kaybı, yakınsama ve
%0,1'den küçük kütle dengesizliği kontrolleri geçti. Bu kontrol TPMS basınç
kaybının ağdan bağımsız olduğunu veya türbülans modelinin deneysel doğruluğunu
kanıtlamaz. Arayüz, süreç iptali ve ilgili davranışlar için 17 test geçti.
Ekran görüntüsü: `design/cylindrical-flow/pressure.png`.

## Tekrarlayan yapılarda mesh optimizasyonu

TPMS ve Truss Lattice alan örneklemesi, örnekleme aralığı hücre periyoduyla
uyumluysa bir hücreyi hesaplayıp diğer örnekleri buradan alır. Bu yol referans
yüzeyi ve HEX8/HEX20 mesh için kullanılır; kısmi son hücreyi de destekler.
Örnekleme aralığı değiştirilmez. Uyumlu olmayan aralıklarda ve Porous yapılarda
doğrudan hesaplama sınırlı büyüklükte bloklarla yapılır. Yüzey için saklanan
skaler alan float32'dir; marching cubes zaten bu hassasiyeti kullanır.

TET4/TET10 için Auto ve Periodic cells seçenekleri TPMS yanında Truss Lattice
yapıları da destekler. Tam sayı periyot, eşleşen hücre yüzleri, eleman kalitesi,
eleman bütçesi ve referansa göre en fazla %1,5 hacim farkı aranır. Auto, bu
koşullar sağlanmazsa Netgen'e döner. Voronoi, rastgele gözenekli yapılar ve Wang
Tile mevcut üreticilerde aynı hücrenin kopyaları değildir; bunlar tek hücreyle
değiştirilmez. Tetra hücre yolu yerel eleman boyutunu tüm hücreye uygular ve her
ayar için Netgen'den hızlı veya daha az elemanlı olacağı garanti değildir.

Ölçüm: uygulamanın Python ortamında `tools/benchmark_periodic_sampling.py`.
Bu ölçüm alan örneklemesini ve referans yüzey üretimini ayrı raporlar;
ANSYS çözüm süresini ölçmez.

## Malzeme eğrisi: gerçek gerilme ve gerinim

Ölçülmüş pekleşme verisi için `True stress–strain (MISO)` seçilir.
Tablonun sütunları boyutsuz **plastik logaritmik gerinim** ve seçili birimde
**gerçek (Cauchy) gerilme** değerleridir. İlk satır sıfır plastik gerinim ve
başlangıç akma gerilmesidir; sonraki gerinimler artmalı, gerilme azalmamalıdır.
Elastik kısım Young modülü ile tanımlanır.

CSV sütun sırası gerinim, gerilmedir. `plastic_strain,true_stress` başlığı
veya başlıksız dosya doğrudan plastik eğri olarak okunur. Toplam gerçek
gerinim için `true_strain,true_stress` başlığı kullanılır; küçük elastik gerinim
varsayımıyla `ε_pl = ε_true − σ_true/E` dönüşümü yapılır. Dosya akma noktasından
başlamalıdır (`ε_true = σ_true/E`); elastik satırlar eklenmez. %0,2 ofset akma
noktasını otomatik olarak sıfıra kaydırma yapılmaz. Böyle bir veri ayrıca
kalibre edilmelidir. Gerilme birimi arayüzde seçilir; başlıkta birim verilirse
seçimle eşleşmelidir (ör. `true_stress_MPa`).

Bu eğri tek eksenli malzeme pekleşme verisidir; yapının global kuvvet–yer
değiştirme eğrisi değildir. Basma verileri pozitif büyüklüklerle girilir.
Mühendislik gerilme–gerinim verisi otomatik dönüştürülmez; boyun verme sonrası
veriler ayrıca malzeme kalibrasyonu gerektirir.

`Bilinear elastoplastic`, ayrıntılı eğri yerine bir yaklaşımdır:
`True yield stress` başlangıç gerçek akma gerilmesi,
`Tangent modulus Eₜ` ise akma sonrası gerçek gerilme–toplam logaritmik gerinim
eğimidir (`0 ≤ Eₜ < E`). Eₜ bir gerinim değildir. Mevcut kayıtların malzeme
modeli ve sayısal değerleri korunur; ölçülmüş veri olmadan eğri üretilmez.

## Mekanik sonuçları 3B dışa aktarma

**Proje → Mekanik 3B sonuçları dışa aktar…** komutuyla kayıtlı analiz
klasörünü (veya `03_Results` klasörünü) ve hedef klasörü seçin. RustMech
son kayıtlı yük adımından yeni bir `RustMech-3D-…` klasörü oluşturur.

- **ANSYS EnSight** için `EnSight/mechanical.case` dosyasını açın. Deforme
  şekli görmek için `Displacement_mm` vektörünü yer değiştirme olarak uygulayın.
  `.case`, `.geo` ve `.var` dosyalarını birlikte saklayın.
- **OpenFOAM ve Kratos sonrası görselleştirme** için ParaView'da
  `ParaView/results_undeformed.vtu` veya `ParaView/deformed_shape.vtu` dosyasını
  açın. Her iki dosya da ağ, mm cinsinden yer değiştirme, MPa cinsinden
  von Mises gerilmesi ve varsa temas alanlarını içerir.

## ANSYS Python bağlantısı

ANSYS analizleri, DoE ve Mesh Convergence için uygulamayı çalıştıran Python
ortamına `requirements-mapdl.txt` paketleri kurulmalıdır. Mevcut başlatıcı,
projedeki `.venv` yoksa `../Gitterburg/.venv` ortamını kullanır:

```powershell
& '..\Gitterburg\.venv\Scripts\python.exe' -m pip install -r requirements-mapdl.txt
```

Bu paketler ANSYS kurulumu ve lisansının yanında gereklidir. `Build-Exe.ps1`,
paketlerin yüklenebildiğini derlemeden önce kontrol eder.

## Plan: geometri sınırına göre kırpılmış voxel mesh

Hedef, voxel yüzeyindeki basamaklı
çıkıntıları gerçek referans geometri sınırına göre kesmek. Tek bağlantılı
elemanların silinmesi bu hedefi karşılamaz. Önce küçük bir örnekte kesilmiş
hücrelerin eleman tipleri, çok küçük hücreler, hacim farkı ve ANSYS aktarımı
değerlendirilecek. Saf HEX20 korunacaksa sınır düğümlerini yüzeye oturtma yolu
ayrıca eleman bozulması açısından incelenecek. Deneme ve çözücüye alma koşulları
[mekanik yöntemlerin uygulama planında](MEKANIK_YONTEM_PLANI.md) yer alır.

## Yön küpü

Sağ üstteki X/Y/Z küpünün yüzleri, kenar ve köşe seçim bölgeleri çizgilerle
ayrılır. Bu bölgelere tıklama yaklaşık yarım saniyelik yumuşak kamera dönüşü
başlatır; küp ve model birlikte döner. Küp sürüklenerek de yön değiştirilebilir.
Odak noktası ve yakınlaştırma korunur. Yeni seçim önceki hareketi günceller;
doğrudan sahne etkileşimi veya View menüsünden yön seçimi hareketi durdurur.

## Precision Light arayüzü

Ana ekran açık renkli Precision Light tasarımına geçirildi. Solda Geometry,
Material ve Analysis sekmeleri, ortada geniş 3B önizleme, altta tek satır ağ
bilgileri bulunur. Geometry / FE mesh / Overlay düğmeleri mevcut gerçek
geometriyi görüntüler. Kamera yönleri, perspektif ve ayrıntılı mesh bilgileri
View menüsündedir. Mesh Convergence ve DoE işlevleri korunur. Planlanan
yöntemlere, üzerinde seçili yöntem adı yazan menüden erişilir.

Arayüz yoğunluğu kompakt tutulur: kontroller 25 px, form satır araları 2 px,
sekmelerle içerik arası 2 px. Stepper'lar Datenburg'un ortalanmış değer alanını
ve mavi oklarını, butonlar aynı uygulamanın hafif kabartmalı kompakt yüzünü
kullanır. Önizleme düğmeleri geometri ayarlarının hemen altındadır.

Güncel kaynak sürümünü `.\Run-RustMech.ps1` ile açın. Mevcut dağıtım EXE'si bu
arayüz değişikliği için yeniden paketlenmedi. Gelecekteki EXE derlemelerine Qt
ok simgeleri de eklenir.

İlk görsel referans `design/precision-light/`, güncel kompakt ekran görüntüleri
`design/compact-datenburg/` içindedir. Etkileşim kontrolünü
`python tools/verify_precision_light.py --output design/compact-datenburg`
ile çalıştırabilirsiniz. Bu kontrol
ANSYS veya başka bir çözücü başlatmaz.

## Otomatik referans geometri

`Surface spacing` artık arayüzde veya kaydedilen ayarlarda bulunmaz. Eski
ayar dosyalarındaki değer okunurken yok sayılır. Referans yüzey örneklemesi
hücre/gözenek ölçeği, yapı ailesi, morfoloji ve katı oranından otomatik hesaplanır;
ince katı duvarlar ve küçük boşluklar için daha sık örnekleme kullanılır.

Global/local mesh boyutu ve HEX8/HEX20/TET4/TET10 seçimi referans geometriyi
değiştirmez. HEX elemanları da her mesh boyutunda aynı örtük yüzey sınırını
örnekler; katı oranını yeniden tutturmak için sınır kaydırılmaz. Kaba mesh daha
köşeli görünebilir ve gerçek mesh katı oranı hedef değerden sapabilir.

Küp yüzlerindeki yüzey kapatma işlemi açık kenar ve dejenere çizgi üretimini
önler; tabla temas düzlemleri tam olarak korunur. Otomatik örnekleme bellek
sınırını aşarsa detayları sessizce azaltmak yerine açıklayıcı hata gösterilir.
Kaynak sürümü `.\Run-RustMech.ps1` ile açın; paketli EXE bu değişikliği içermez.

## İlk Netgen üretiminin hızlandırılması

İlk ağ üretiminde artık kaynak koddan optimize edilen Netgen çekirdeği kullanılır.
Atlas yakınlık aramaları, sınır kesişimi için aday bulma, tam eşleşen STL köşeleri
ve tek iş parçacığındaki düğüm–eleman tabloları hızlandırıldı. Netgen'in mesh
boyutları, kalite ölçütleri ve optimizasyon geçişleri korunur.

264.260 üçgenli sabit Gyroid yüzeyinde, global/local boyut 0,7 / 0,35 mm:

| İlk üretim işlemi | Standart Netgen | Optimize çekirdek |
| --- | ---: | ---: |
| Netgen hesabı | 18,539 s | 15,235 s |
| Sınır verisinin aktarımı dahil çağrı | 23,252 s | 19,914 s |

Her tekrar **yeni Python sürecinde**, aynı işlemci çekirdeği, yüzey ve inceltme
noktalarıyla yapıldı. Tablo üç tekrarın medyanıdır; önceki hacim ağı yeniden
kullanılmadı. Netgen hesap süresi %17,8, hazırlık dahil süre %14,4 azaldı.
Her iki sürümde 9.611 tetrahedron ve 3.536 düğüm oluştu; bütün koordinatlar ve
eleman bağlantıları bit düzeyinde eşleşti. Bu nedenle geometri, temas düğümleri
ve kalite değerleri de korunur. Yüzey/ince-duvar örneklemesi, çizim ve ANSYS çözümü
bu zamanlara dahil değildir. Yüzey aralığı uygulamada otomatik olmaya devam eder;
ölçümde iki çekirdeği karşılaştırmak için sabit bir referans yüzey kullanıldı.

Kanıt ve tekrar üretim: `netgen_first_mesh_final.json`,
`benchmark_netgen_cold.py`, `native/netgen_kernel/README.md`.
48 Python testi, 37.054 yerel referans kontrolü ve kaynak arayüz kontrolü geçti.
Yeni DLLler `native/bin/netgen_kernel` içindedir; kurulu Python Netgen/OCCT
paketine müdahale edilmez. Dosya/sürüm doğrulaması başarısızsa standart Netgen
yolu kullanılır. Kaynak derleme ve karşılaştırma:

```powershell
.\Build-NetgenKernel.ps1 -Check -Install
python benchmark_netgen_cold.py --fixture benchmarks/netgen_kernel/netgen_first_mesh_comparison --surface-spacing .075 --repeats 3 --output netgen_first_mesh_final.json
```

EXE paketlenmedi. Güncel sürümü `.\Run-RustMech.ps1` ile açın.

## Önceki tur: Netgen aktarımı ve TET4/TET10 geçişleri

EXE paketlemesi kullanıcı talebiyle sonraya bırakıldı. Güncel sürümü
`.\Run-RustMech.ps1` ile açın. Kaynak Rust çekirdeği ABI 4, mevcut EXE'nin
özel çekirdeği ABI 2'dir; dağıtımların DLLlerini birbirine kopyalamayın.

- Netgen'e yüzey ve yerel inceltme noktaları toplu NumPy tamponlarıyla verilir.
  ASCII STL yazma/okuma kaldırıldı. Netgen'in C++ nesnesini oluşturan küçük
  köprü `native/netgen_bridge` içindedir; facet normalleri Rust'ta, eski STL
  yazıcısının aynı 64 bit aritmetiğiyle hesaplanır.
- TET10 ortak kenar düğümleri Rust'ta doğrudan oluşturulur. Önceden Netgen
  bu noktaları eğri yüzeye taşıyor, uygulama daha sonra kenar ortasına geri
  alıyordu. Son eleman geometrisi eski dönüşümle birebir eşleşir; orta düğüm
  numaralandırması değişebilir. ANSYS düğüm/eleman etiketleri yeniden üretilir.
- Son doğrusal hacim ağı en fazla 128 MiB'lik tek bir önbellekte tutulur.
  TET4/TET10 geçişi hacmi yeniden üretmez. Gerçek yüzey koordinatları ve
  üçgenleri, numune boyutu, yüzey aralığı ve iki mesh boyutu anahtara dahildir.
  Dönen gridler bağımsız tamponlara sahiptir. İptalde işçiyle birlikte önbellek
  de kapanır; sonrasında normal üretim yapılır.

Aynı Gyroid örneği: boyut 6 mm, yüzey aralığı 0,15 mm, global/local mesh
boyutu 0,7 / 0,35 mm; 18.446 tetrahedron, TET4 için 6.655, TET10 için 38.507 düğüm.

| İşlem | Önce | Güncel kaynak |
| --- | ---: | ---: |
| Netgen'e veri aktarımı / hazırlık | 1,863 s | 0,833 s |
| Aynı ağda TET4 → TET10, gerçek önizleme işçisi | her seferinde hacim üretimi | 15,6 ms |
| Aynı ağda TET10 → TET4, gerçek önizleme işçisi | her seferinde hacim üretimi | 9,6 ms |
| Önbelleksiz tam TET10 üretimi | 15,931 s | 15,752 s |

İlk ve son satır üç tekrarın medyanıdır; gerçek işçi geçişleri tek ölçümdür
ve ortak bellek aktarımını içerir, çizimi içermez. Netgen'in ilk hacim üretim
çekirdeği aynı C++ algoritmasıdır; bu ölçümde ilk üretimin toplam kazancı
küçüktür. En büyük kazanç tekrar üretimi kaldırmak ve TET10 dönüşümündedir.
Mesh boyutları ve Netgen'in kalite/optimizasyon geçişleri değiştirilmedi.
En küçük scaled Jacobian 0,033468; ters/sıfır hacimli eleman yok.
Ölçümler `tetra_acceleration.json` ve `tetra_preview_acceleration.json` içindedir.
37 Python ve 9 Rust testi, Ruff/Clippy ve kaynak arayüz kontrolü geçti.
Yeni testler ortak kenar düğümlerini, eski/yeni TET10 eleman geometrisini,
temas düğümlerini, önbellek sınırını/geçersizleştirmesini, bağımsız tamponları
ve Netgen köprüsünün uyumluluk kontrolünü kapsar.

Köprü, derlendiği Python ve Netgen sürümünü ve Netgen DLL hash'ini doğrular.
Eksik veya uyumsuz köprüde eski ASCII yoluna otomatik dönülür. Kaynak derleme:

```powershell
.\Build-MeshKernel.ps1 -Check
.\Build-NetgenBridge.ps1
python benchmark_tetra_acceleration.py --repeats 3
python benchmark_tetra_acceleration.py --preview-only --output tetra_preview_acceleration.json
```

## İkinci hızlandırma turunun ölçümleri

Bu tur **EXE güncellenmedi**; kullanıcı talebiyle paketleme sonraya bırakıldı.
Yeni sürümü kaynak koddan `.\Run-RustMech.ps1` ile çalıştırın. `Start.cmd`
mevcut EXE paketini açmaya devam eder. Kaynak Rust çekirdeği ABI 4, paketli eski
çekirdek ABI 2'dir; DLLleri iki dağıtım arasında elle değiştirmeyin.

Önceki RustMech kaynak sürümüyle aynı makine ve geometride karşılaştırma:

| İşlem | Önce | Güncel kaynak |
| --- | ---: | ---: |
| İlk önizleme | 2,193 s | 0,799 s |
| Mesh boyutu değişince önizleme | 53,6 ms | 13,1 ms |
| Varsayılan yüzey hazırlama | 761 ms | 277 ms |
| 100³ HEX20 üretimi | 116 ms | 47,9 ms |
| 150³ HEX20 üretimi | 384 ms | 228 ms |
| HEX20 APDL yazımı, 64.798 eleman | 642 ms | 262 ms |

Yüzey/mesh süreleri beş tekrarın, APDL üç tekrarın medyanıdır. Önizleme süreleri
tek gerçek işçi çalışmasının ölçümüdür. Grafik çizimi ve ANSYS çözüm süresi bu
ölçümlere dahil değildir. Dosyalar: `efficiency_before.json`,
`efficiency_after.json`, `preview_before_round3.json`, `preview_after_round3.json`;
birleştirilmiş karşılaştırma `efficiency_comparison.json` içindedir.

- HEX20 yalnız köşe ve kenar düğümlerini indeksler. 150³ için indeks tamponu
  104,0 → 52,3 MiB; bileşen kuyruğu ve voxel kökenleri de 32 bit olarak tutulur.
  Tabla düğümleri Rust'ta oluşturulurken bulunur; büyük `isclose` geçişleri yoktur.
- Yüzeyde tam eşleşen noktalar ve en büyük bağlantılı bileşen Rust'ta bulunur.
  Kapalı yüzeylerde marching-cubes yönlendirmesi kullanılır. Açık/manifold olmayan
  veya dejenere girdiler eski VTK temizleme ve yön onarımına döner; doğruluk korunur.
- TPMS formülü bir kez seçilir; iç voxel döngüsünde formül seçimi ve tekrar eden
  bölmeler kaldırılır. X/Y trigonometrik tabloları satır boyunca yeniden kullanılır.
- Geometri motoru `mesh_engine.py` içindedir; işçi Qt veya Matplotlib yüklemez.
  İşçi arayüz kurulurken ısınır, Matplotlib yalnız grafik gerektiğinde yüklenir.
- Önizleme bir ortak bellek tamponuyla toplu aktarılır. Ebeveyn sıradan NumPy
  dizilerine kopyaladıktan sonra tampon kapanır. Grid, sığ kopya ve çözücü verileri
  işçinin ömründen bağımsızdır. İptal ve hata yolları da tamponu temizler.
- APDL sayıları aynı `.16g` yuvarlamasıyla yığın tamponunda biçimlenir;
  koordinat başına geçici String tahsisleri kaldırılır.

29 Python ve 8 Rust testi geçti. Geometri, yüz yönü ve normaller TPMS/kafes/
gözenekli örneklerde referansla karşılaştırıldı; varsayılan yüzeyin hızlı yolunda
üçgenler ve normaller birebir eşleşiyor. Ortak bellek ömrü, hata sonrası temizleme,
iptal, ön ısıtma, yeniden başlatma ve ANSYS dosya baytları denetleniyor. Kaynak
arayüz testi ve 17.719 elemanlı TET10 çevrimdışı kontrolü geçti; geçersiz eleman yok.

Netgen için 1/8 iş parçacığı deneyi `netgen_modes.json` içinde: bu STL örneğinde
8 iş parçacığı hız kazandırmadığı için üretim ayarı değiştirilmedi.

Yeniden ölçmek için:

```powershell
python benchmark_efficiency.py --output efficiency_latest.json
python benchmark_preview.py
python -m unittest discover -s tests -v
```


RustMech, periyodik ve gözenekli yapıların mesh üretimi, önizlemesi ve ANSYS
MAPDL ile doğrusal olmayan basma analizi için masaüstü uygulamasıdır.

## Başlatma

`Start.cmd` dosyasını açın veya `dist/RustMech/RustMech.exe` dosyasını çalıştırın.
EXE ile yanındaki `_internal` klasörünü birlikte tutun. Kaynak koddan başlatmak
veya yeniden derlemek için:

```powershell
.\Run-RustMech.ps1
.\Build-MeshKernel.ps1 -Check
.\Build-Exe.ps1
```

### Windows kurulumu oluşturma

`dist/SetupMaker.exe` dosyasını açın, hedef sürümü girin ve **Kurulum oluştur**
düğmesine basın. Kurulum EXE'si `MyDrive/X-MySoftware/RustMech` klasörüne yazılır.
Paketleme geçici alanda yapılır ve işlem günlüğü pencerede görünür.

**Kurulumdan sonra GitHub'a yükle** kutusu açılışta işaretlidir. Sadece Drive
çıktısı almak için kutunun işaretini kaldırın. GitHub yayını, oturum açmış
GitHub CLI ve hedef depoda yayın izni gerektirir. Varsayılan depo
`aserdargl/RustMech-Releases`'tir.

Setup Maker'ı kaynak koddan çalıştırmak veya yeniden derlemek için RustMech'in
derleme bağımlılıklarının bulunduğu Python ortamında aşağıdaki komutları
kullanın. Kurulum oluşturmak için Inno Setup 6.3 veya üzeri gerekir.

```powershell
python -m tools.setupmaker.main
python -m tools.setupmaker.build
```

Proje klasörü `D:\MySoftware\RustMech` olarak adlandırılmıştır. PyCharm modül adı,
pencere başlığı, uygulama kimliği, EXE dosyası ve Windows ürün bilgisi RustMech'tir.
Eski dağıtımlar ve sonuçları `LegacyDistributions` altında korunur. Yeni çalışma
sonuçları kaynak uygulamada proje klasörünün, EXE'de EXE klasörünün yanına yazılır.
Tanı dosyası `RustMech.log` adını taşır.

Kaynak uygulamanın Python ortamında NumPy, SciPy, PyVista, scikit-image ve PySide6
bulunmalıdır. TET4/TET10 için `requirements-tet10.txt` gereklidir. Mevcut PyCharm
Python ortamı `D:\MySoftware\Gitterburg\.venv\Scripts\python.exe` konumundadır.

## Rust çekirdeği

`native/mesh_kernel` içindeki çekirdek `native/bin/rustmech_core.dll` olarak
derlenir ve `rust_mesh.py` üzerinden sürümlenmiş C ABI ile çağrılır. Python sonuç
belleğini ayırır; Rust bu tamponlara doğrudan yazar, hesap sırasında GIL bırakılır.
`RUSTMECH_CORE_LIBRARY` farklı DLL konumu, `RUSTMECH_THREADS` geometri ve gerilme
çekirdeklerinin iş parçacığı sayısı için kullanılabilir. Varsayılan üst sınır sekizdir;
bu sayı ANSYS'in lisanslı çözücü çekirdeği sınırından bağımsızdır.

Rust'ta yürütülen işlemler:

- HEX8/HEX20 voxel seçimi, altı komşulu bileşen ayrımı, ortak düğüm ve eleman üretimi.
- 19 TPMS denkleminin birleştirilmiş aritmetiği; sinüs/kosinüs eksen tabloları NumPy'da
  hazırlanır, eski kayan nokta işlemlerinin sırası korunur.
- Periyodik graf tabanlı kafeslerin çubuklara uzaklık hesabı.
- Gözenekli referans hacimlerin doğrusal örneklemesi; büyük koordinat tabloları oluşmaz.
- Sonuç düğümü kimlik eşleştirmesi; ardışık kimliklerde doğrudan indeksleme,
  sıralı kimliklerde ikili arama, karışık kimliklerde hash tablosu.
- Tetrahedral hacim/yön ve kenar oranı hesabı; von Mises gerilmesi.
- Tamponlu ANSYS APDL mesh dosyası yazımı; Python `.16g` biçimi ve EN/EMORE düzeni korunur.

Düzenli eksen örneklemesi dışındaki genel koordinat çağrıları mevcut NumPy/SciPy
uygulamasını kullanabilir. BCC/FCC/Cubic analitik alanlar, Voronoi komşuluk araması,
FFT ve Gauss filtreleri mevcut derlenmiş NumPy/SciPy çekirdeklerini kullanır.
Tetrahedron üretimi Netgen'de; yüzey çıkarımı, normal ve yerel mesh kalitesi VTK /
scikit-image'dadır. ANSYS çözümü MAPDL'de kalır. Arayüz, ayarlar ve iş akışı Python'dadır.

Önizleme artık kalıcı ve iptal edilebilir bir işçi süreci kullanır. İşçi son yüzey
ve mesh'i bellekte tutar; her güncellemede Python importları yapılmaz ve değişmeyen
büyük veri ebeveyne yeniden gönderilmez. İptal, yerel hesap sürerken süreci durdurur;
bir sonraki istek yeni işçi oluşturur. Pencere kapandığında işçi temizlenir.

## İlk RustMech sürümünün ölçülen kazançları

Intel Core i9-14900K / Windows x64 üzerinde, 100³ örnekleme için eski uygulama ile
aynı girdiler karşılaştırıldı. Aşağıdaki değerler tek makinedeki aşama ölçümleridir;
tüm programın veya ANSYS çözümünün hızlanma oranı değildir.

| İşlem | Önce | Sonra | Hızlanma |
| --- | ---: | ---: | ---: |
| Gyroid alanı (seyrek NumPy eksenlerine karşı) | 4,22 ms | 2,94 ms | 1,4 kat |
| Fischer Koch Y alanı | 14,00 ms | 3,08 ms | 4,5 kat |
| BCC-Z kafes alanı | 440 ms | 12,2 ms | 36,1 kat |
| X-Braced Cubic kafes alanı | 1.051 ms | 35,4 ms | 29,7 kat |
| Gözenekli hacim örneklemesi | 301 ms | 18,2 ms | 16,6 kat |
| 1 milyon sonuç düğümü eşleştirme | 590 ms | 28,8 ms | 20,5 kat |
| 1 milyon von Mises hesabı | 56,5 ms | 3,42 ms | 16,5 kat |
| HEX20 APDL dışa aktarımı, 64.798 eleman | 11,79 s | 0,72 s | 16,3 kat |
| Mesh boyutu değişince gerçek önizleme | 1,48 s | 0,053 s | yaklaşık 28 kat |

Kafes/gözenekli/gerilme/eşleştirme testlerinde beş, dosya yazımında üç tekrarın
medyanı kullanıldı. Önizleme değeri gerçek süreç ve veri aktarımı dahil tek çalışma
ölçümüdür. İlk açılış yaklaşık 2,14 saniyedir; kalıcı işçi ilk açılış maliyetini kaldırmaz.

İlk mesh taşımasındaki 150³ / 1.012.430 eleman ölçümünde tam hacim mesh üretimi
HEX8 için 865 → 103 ms, HEX20 için 1.639 → 248 ms olmuştu. Tepe çalışma kümesi
HEX8'de 650 → 379 MiB, HEX20'de 1.246 → 629 MiB idi. Bu önceki ölçümler
`mesh_benchmark.json` ve `mesh_memory_benchmark.json` dosyalarında korunur.

## Doğrulama ve yeniden ölçme

Ana ekranın **Setup** panelinde, **Mesh** grubunun hemen altındaki
**Solver and loading** grubundan deplasman (mm) veya kuvvet
(N) kontrolü seçilir. Deplasman kontrolünde kuvvet, kuvvet kontrolünde deplasman
hesaplanır; aynı serbestlik derecesine ikisi birden uygulanmaz. Kuvvet kontrollü
yakınsama değerlendirmesi hesaplanan deplasmanı karşılaştırır.
**Material** grubunun altındaki **Contact and friction** grubunda alt ve üst plaka için ayrı temas türleri
(sürtünmeli, sürtünmesiz, rough veya başlangıçta bağlı) ve sürtünme katsayıları
tanımlanır. İç yüzey teması, temas algoritması, sönümleme ve plaka/iç yüzey
penetrasyon toleransları ayrıca ayarlanabilir. Koşullar tüm ağ seviyelerinde sabit
kalır ve CSV çıktısına yazılır. **Use this mesh**, doğrulanan ağı ve analiz
koşullarını ana çalışma alanına aktarır. Mesh Convergence bu koşulları ana
ekrandan alır; içinde ayrı yükleme veya temas ayarları bulunmaz.

Temasın sayısal parametreleri başlangıçta **Automatic defaults** ile uygulamanın
önerilen değerlerini kullanır. Elle düzenlemek için bu seçeneği kapatın; otomatik
modu açıp kapatmak önceki özel değerlerinizi korur. CPU çekirdek sayısındaki **Auto**
seçeneği seçili çözücünün lisans sınırına uygun varsayılanını kullanır. Alt adımlar
çözüm sırasında otomatik uyarlanır; **Advanced…** başlangıç ve sınır değerlerini açar.
Sürtünme katsayıları, temas türleri, malzeme ve yükleme fiziksel girdiler olarak
elle seçilir. Eski dosyalardaki özel sayısal değerler otomatik modla ezilmez.

Lisanslı küçük blok ve Gyroid kontrolleri için `python tools/verify_loading_contact.py`
çalıştırılabilir; bu komut gerçek ANSYS çözümleri başlatır ve kayıtlarını
`design/loading-contact-verification` altında ayrı klasörlerde tutar.

```powershell
python -m unittest discover -s tests -v
cargo test --manifest-path native/mesh_kernel/Cargo.toml
cargo clippy --manifest-path native/mesh_kernel/Cargo.toml --all-targets -- -D warnings
python nonlinear_compression.py --offline-test --no-images
python benchmark_acceleration.py
python benchmark_preview.py
python benchmark_mesh.py --sizes 30 60 100 150 --repeats 7
python benchmark_tetra.py --full
```

22 Python ve 6 Rust testi; geometri eşdeğerliğini, TPMS denklemlerini, kafesleri,
gözenekli örneklemeyi, ID hatalarını, bellek sahipliğini, seri hale getirmeyi,
işçi önbelleğini, iptal/yeniden başlatmayı, TET4/TET10 ve ters elemanları kontrol eder.
APDL çıktıları dört eleman türünde, rastgele sayılar ve geniş üs aralığında eski
Python yazıcısıyla bayt düzeyinde karşılaştırılır. Yeni ölçümler
`acceleration_benchmark.json` ve `preview_benchmark.json` içindedir.

Çevrimdışı kontroller ANSYS lisanslı çözümü başlatmaz. Lisanslı çözüm için yerel
ANSYS MAPDL kurulumu ve lisansı gerekir; bu geliştirmede lisanslı çözüm yürütülmedi.

Paketli EXE de doğrulandı: RustMech pencere/uygulama adı, önizleme, çevrimdışı HEX8
ve 17.719 elemanlı TET10 kontrolü geçti; geçersiz eleman bulunmadı. Paketleyici
Netgen OCCT DLLlerini toplar ve wheel metadata yollarını taşınabilir hale getirir.
Qt için uyumsuz Poppler ICU DLLsi etkin yükleme yolundan çıkarılır; Windows ICU kullanılır.

Taşıma sonrası testler ve EXE arayüz kontrolü yeni konumda tekrar geçti. Eski
`D:\MySoftware\AnsysPython` klasörü etkin oturum kilidi nedeniyle boş olarak kaldı;
projenin tüm dosyaları RustMech konumundadır. PyCharm için RustMech klasörünü açın.

### TRUBA süreli kaynak oturumu

TRUBA panelinde bağlantıyı kurun, kuyruk (Partition), hesap, CPU, bellek ve
Wall time değerlerini belirleyip **Allocate resources** düğmesine basın.
Yeni oturumların varsayılan süresi `04:00:00` (dört saat), kuyruk seçimi **Auto**
olur. Kaydedilmiş kuyruk seçimi ve etkin tahsisin süre sınırı korunur.
Auto, hamsi/barbun/orfoz CPU kuyruklarını gerçek iş oluşturmayan
`sbatch --test-only` ile doğrular ve Slurm'un tahmin ettiği en erken başlangıcı
seçer. Tahmin yoksa canlı CPU/bellek durumunda uygun boş kapasiteyi tercih eder.
GPU kuyrukları mevcut CPU çözücüleri için ayrılmaz; ücretli öncelik veya ek
hizmet talebi gönderilmez. Tahmin değişebilir; kuyruğun ve hesabın
istenen süreyi ve kaynak miktarını kabul etmesi gerekir. İlk tahsis Slurm'da bekler.
**Refresh session** kayıtlı oturumu bu çalıştırmaya bağlar ve durumu yeniler.

Bağlanmak veya TRUBA hesaplama modunu işaretlemek tek başına Slurm işi
oluşturmaz. Oturum içinde çalışan işlemler için bir kaynak oturumu ayırın
veya kayıtlı oturumu yenileyin.
Allocate kaynak isteğiyle TRUBA hesaplama modu açılır. Oturum RUNNING ve işçi
hazır olduğunda uzak ortam bir kez otomatik kontrol edilir; gerekirse aynı tahsiste
otomatik hazırlanır ve doğrulanır. Başarısız kurulum kendiliğinden sürekli
tekrarlanmaz; hata ayrıntısı Advanced durum alanında gösterilir. Üst başlık
Slurm tahsisi RUNNING olarak doğrulanınca **yeşil RUNNING** gösterir; işçi ve
üretim ortamı da doğrulanınca **yeşil READY** olur. RUNNING sırasında işçinin
başlaması, ortam kontrolü veya kurulum gereksinimi ayrıca yazılır; hesaplama
yalnız READY koşulları sağlandığında açılır. Kuyrukta veya tahsis doğrulanmamışsa
sarıdır. Yalnız SSH bağlantısı yeşil READY sayılmaz.
Hazır olma bildirimi üretim başlatmaz. **Apply** geometriyi aynı kaynağın
`surface` adımında (büyükse otomatik chunked/periyodik yolla) üretir; VTP sonucu
indirilip yerel ekranda çizilir. FE Mesh isteği ayrıca hacim ağını üretir.
Ağ önizleme ve DoE ağ kontrolü kaynak oturumunda `srun` adımı olarak yürütülür.
Mekanik, FFT, CFD, CHT, DoE ve analiz kuyruğundaki çözümler kendi adlandırılmış
betikleri ve Slurm iş kimlikleriyle ayrı `sbatch` işleri olarak gönderilir.
**Mesh Convergence** her seviyeyi ayrı bir iş olarak gönderir. **Allocate**
gerekmez. **CPUs per analysis** varsayılanı 16'dır ve
kullanıcı bunu değiştirebilir. Slurm betiği, gerekirse kuyruk alt sınırı kadar
CPU ister. Aynı grupta gönderilen seviye sayısı, bağlantı panelindeki CPU ve RAM
bütçesine göre belirlenir ve beşi geçmez. Sonuç tablosu ve CSV iş kimliğini,
betiği ve durumu gösterir. Betik hücresinde ayrıca Slurm'dan istenen CPU ve
Kratos'un çalışma ayarı görünür. **Advanced → Wall time per analysis** her
seviyenin Slurm süresini belirler. Varsayılan 30 dakikadır. Süreyi seçili
kuyruğun sınırları içinde değiştirebilirsiniz.
**Study time budget** yalnızca yerel mesh convergence çalışmasına uygulanır.
Tahsis öncesinde **Parallel analyses** ile DoE ve analiz kuyruğunda aynı anda
çalışabilecek analiz sayısını seçin (varsayılan `1`). CPU ve RAM işlere
paylaştırılır. Kuyruğun CPU alt sınırı, bir iş için ayrılacak CPU sayısını
artırabilir. Bellek gereksinimine göre daha az eşzamanlı
analiz seçin. Her adım ayrı CPU/bellek sınırı ve sonuç klasörü kullanır;
boşalan paya sıradaki analiz alınır. Tamamlanma sırası değişse de sonuçlar
doğru satıra yazılır ve kaydedilir. Tek büyük analiz, önizleme ve ortam
hazırlığı tüm tahsisi kullanır. Kaynak ayarları oturum boyunca sabittir.

Boşta geçirilen süre de tahsis süresine dahildir. **Prepare replacement resources
near the time limit** varsayılan açıktır: DoE ağ kontrolleri çalışırken son bir
saatte (kısa tahsislerde son %10'da) aynı kaynak ayarlarıyla tek bir yedek
tahsis istenir. Kuyruk izleyicisi açıkken bu kontrol yapılır; yalnızca boşta
duran oturum kendiliğinden yenilenmez. Yeni ağ kontrolleri yedek işçi hazır
olana kadar bekler, sonra ona yönlendirilir. Başlamış kontroller eski tahsiste
devam eder. Biten kontroller tekrar çalıştırılmaz. Slurm başlangıç zamanını
belirlediğinden beklemesiz geçiş garanti edilmez. Bu özellik çalışan çözücünün
ara hesap adımını taşımaz. Slurm, eski tahsisin süresi dolduğunda hâlâ çalışan
analizleri durdurur. Kaldığı adımdan devam etmek çözücü checkpoint/restart
desteği gerektirir. Eski tahsis süresi bitene veya kaynaklar bırakılana kadar
ayrılmış kalabilir.

Bir analizi iptal etmek diğer analizleri veya kaynak oturumunu kapatmaz.
Kuyruğun Stop düğmesi tüm etkin kuyruk analizlerini durdurur.
**Release resources**, hazırlanan yedek ve önceki tahsisler dahil bu oturumun
kaynaklarını ve içindeki işleri sonlandırır. **Normal kapanış**, bu çalıştırmada ayrılan veya açıkça
Refresh session ile bağlanılan RustMech tahsisini de otomatik bırakır; henüz
kuyrukta bekleyen veya gönderimi tamamlanmamış istek de kapsanır. Kapanış
arka planda iptali ve yalnız ilgili job ID'sinin sonlandığını doğrular.
Doğrulama başarısızsa pencere açık kalır; bağlantıyı düzeltip yeniden kapatın.
Başlangıçta yalnız diskten okunan, bu çalıştırmaya bağlanılmamış eski tahsise
dokunulmaz. İşletim sisteminin süreci zorla öldürmesi/çökmesi normal kapanış
akışını çalıştıramaz; kalan kayıt sonraki açılışta doğrulanıp bırakılabilir.
Bağlantı hatasında kayıtlı işi yenileyin veya geri alın; tekrar kaynak talep
ederek belirsiz bir gönderimin kopyasını oluşturmayın.

Oturum protokolü ve iptal kontrolleri:
`python -m unittest tests.test_truba_close tests.test_truba_session tests.test_truba_remote tests.test_remote_mode_ui`.

## Imported geometry and compression faces

In **Structure / Dimensions**, choose **Import geometry…** to open a closed,
single-solid STL, OBJ, PLY or VTP surface. Coordinates are interpreted in mm.
Right-click the support end face, then the opposite compression end face.
Green marks the support and orange marks the compression surface. Coplanar
triangles are selected together. The two faces must be parallel and must bound
the entire specimen. Arbitrary curved contact surfaces are not supported by
this planar compression workflow.

Choose **Use geometry** to retain a per-user copy, align the selected compression
direction with Z without scaling, and build a surface-conforming TET4 mesh.
TET10 is also available. Force/displacement and contact settings continue to
apply to the two platens. Imported parts use their bounding rectangle for nominal
stress and their bounding volume for relative density. TRUBA requests include
the imported surface. **Use generated structure** returns to the structure generator.
The import window remembers its size, position and maximized state.
This planar-face import workflow applies to mechanical compression. Flow and
CHT also accept closed imported surfaces through their shared geometry workflow.


## Bağımlılıklar ve sürüm kontrolü

`requirements-base.txt`, çalışan geliştirme ortamındaki (Python 3.14.10) temel paket
sürümlerini kaydeder; temiz ortamda kurulum henüz denenmedi. İsteğe bağlı gruplar
`requirements-truba.txt`, `requirements-tet10.txt` ve `requirements-mapdl.txt`
dosyalarındadır. Proje git deposundadır; kullanıcı sonuçları (`HumanResults/`,
`results/`), `dist*/`, `build/`, ikililer ve kök günlükleri `.gitignore` ile dışarıda
tutulur. Eski kök günlükleri `build/logs/2026-11-kok-arsivi/` altına taşındı;
`RustMech.log` çalışma günlüğü olarak kökte kalır.

### Malzeme kütüphanesi

Model > Material bölümünde **Library / Kütüphane** düğmesi hazır kartları açar.
Soldaki listeden veya arama alanından malzemeyi seçin; sağda SI birimleriyle
özellikler, üretim koşulu, referans sıcaklığı ve tıklanabilir teknik föy/kaynak
bağlantıları görünür. Kaynaklar ayrı bir panelde her zaman erişilebilirdir.
**Add / Ekle**, kartı malzeme açılır listesine ekleyip seçer; analiz başlatmaz.
Aynı kart iki kez eklenmez. Eklenen seçenekler kullanıcıya özel saklanır;
etkin malzeme ve kaynak bilgisi mevcut ayar/proje/malzeme dosyasıyla taşınır.
Malzemeler arasında geçiş, oturumdaki düzenlenmiş değerleri korur.

Başlangıç kataloğu: 316L, Ti-6Al-4V Grade 5, AlSi10Mg, 6061-T6, S355,
Cu-ETP/C11000, Prusament PLA, EOS PA 2200/PA12, **Inconel 625 ve 718**,
**Anycubic ABS-Like Resin 3.0 (SLA/MSLA)**, su ve kuru hava.
Akışkan kartları Flow/Thermal bölümündeki akışkan Library düğmesinden;
ısıl katı kartları Thermal bölümündeki katı Library düğmesinden seçilir.

Kartlar sabit referans özellikleri kullanır; katılar doğrusal elastiktir.
Mühendislik tahminleri özellik tablosunda işaretlenir ve açıklanır. Reçinenin
elastisite modülü üreticinin ürün sayfasındaki aralığın orta değeridir;
yoğunluğunun kürlenmiş numuneye ait olduğu TDS'de belirtilmediği için yoğunluk
ve Poisson oranı tahmin olarak işaretlenir. Eksik termal özellikler başka
malzemenin değerleriyle doldurulmaz; gerekli özellikleri eksik kartın termal
atanması engellenir. Katalog `material_catalog.py` içinde çevrimdışı bulunur;
kütüphaneyi açmak ağ isteği yapmaz. Pencere ve iki bölücünün yerleşimi saklanır.

Odaklı kontrol: `python -m unittest tests.test_material_catalog_ui tests.test_material_units tests.test_material_library tests.test_workspace_materials`.

### Test grupları

`python tools/run_tests.py core` ekran gerektirmeyen testleri (Qt offscreen), `gui`
ise VTK/OpenGL penceresi açan testleri gerçek masaüstü oturumunda çalıştırır;
`--python` ile yorumlayıcı seçilir. Her dosya ayrı süreçte ve süre sınırıyla koşar,
tümü başarılıysa çıkış kodu 0'dır. `RUSTMECH_OFFLINE=1` varsayılandır (loopback SSH
testi atlanır). Yeni bir pencere testi offscreen modda VTK pixel-format hatasıyla
düşerse `tools/run_tests.py` içindeki `GUI` listesine eklenmelidir.

### CFD için yüzeye oturan OpenFOAM ağı

Flow bölümünde **Fluid mesh → Fitted · OpenFOAM** seçin. Boruda dairesel
O-grid, kare/dikdörtgen kanalda blok ağ kullanılır; dolgu snappyHexMesh ile
yakalanır. **Surface refinement** dolgu yüzeyinin inceltme seviyesini,
**Wall layers** istenen katman sayısını, **Fitted cell budget** üst hücre
bütçesini belirler. **Build** yalnız ağı üretip kontrol eder; **Run** ayrıca
akışı çözer. Eski kayıtların varsayılanı Kartezyen ağdır.

Sonuçta gerçek hücre sayısı, katman yüz kapsamı ve sınır başına ortalama
katman sayısı görünür. İnce/karmaşık dolguda bazı yüzlerde katman oluşmayabilir;
uyarıları ve `log.snappyHexMesh` dosyasını inceleyin. İstenen katmanların hiçbiri
oluşmazsa veya dolgu inceltmesi sağlanmazsa çözüm durur. SST için gerçek y+
istatistikleri ve `postProcessing/yPlus` çıktısı saklanır. checkMesh geçmesi
ağ bağımsızlığı veya fiziksel doğruluk garantisi değildir. Sonuçtaki katı
hacim oranı hâlâ Kartezyen ön kontrolün örneklemesidir.

Yerelde OpenCFD/WSL ortamında blockMesh, snappyHexMesh, checkMesh ve simpleFoam
gerekir; uzak çalışmada mevcut OpenFOAM modülü kullanılır. OpenFOAM vaka
klasörünün yolunda boşluk bulunmamalıdır. Canlı TRUBA çözümü bu teslimde
çalıştırılmadı. CHT için yüzeye oturan çok bölgeli ağ henüz desteklenmez;
CHT Kartezyen ağla devam eder. Dağıtım EXE'si yeniden paketlenmedi.

Tekrarlanabilir küçük çözücü kontrolleri:
`python tools/verify_fitted_flow.py --output-root D:/MySoftware/RustMech/build`.

## License

RustMech is free software licensed under the GNU General Public License v3.0 or later (GPL-3.0-or-later). See LICENSE.
