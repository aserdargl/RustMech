"""English source text and Turkish presentation catalog (solver identifiers stay unchanged)."""
from setup_help import GEOMETRY_HELP

_TEXT = '''
MPM TPMS Compression|MPM ile TPMS Basma
Uses the TPMS from Model. The lower platen is fixed and the upper platen moves along −Z.|Model sekmesindeki TPMS kullanılır. Alt plaka sabittir ve üst plaka −Z yönünde hareket eder.
Loading and discretization|Yükleme ve ayrıklaştırma
Platen travel|Plaka stroku
Loading time|Yükleme süresi
Background cell size|Arka plan hücre boyutu
Points per cell axis|Hücre ekseni başına nokta
MPM solid material|MPM katı malzemesi
Young’s modulus|Young modülü
Copy Model properties|Model özelliklerini kopyala
Compressible Neo-Hookean material. Frictionless penalty contact at both platens.|Sıkıştırılabilir Neo-Hookean malzeme. İki plakada da sürtünmesiz ceza teması.
Run MPM|MPM’yi çalıştır
Open MPM result…|MPM sonucunu aç…
Open MPM result|MPM sonucunu aç
Install local Kratos MPM|Yerel Kratos MPM’yi kur
Force–displacement curve|Kuvvet–yer değiştirme eğrisi
Platen reaction / N|Plaka reaksiyonu / N
Upper platen|Üst plaka
Lower platen|Alt plaka
Run MPM or open a saved result to view the force–displacement curve.|Kuvvet–yer değiştirme eğrisi için MPM’yi çalıştırın veya kayıtlı bir sonucu açın.
Install the local Kratos MPM runtime from the MPM workspace.|Yerel Kratos MPM çalışma ortamını MPM sekmesinden kurun.
Connect to TRUBA and prepare its Kratos MPM runtime.|TRUBA’ya bağlanın ve Kratos MPM çalışma ortamını hazırlayın.
Installing local Kratos MPM…|Yerel Kratos MPM kuruluyor…
Local Kratos MPM is ready.|Yerel Kratos MPM hazır.
Copied the Model elastic properties into MPM.|Model elastik özellikleri MPM’ye kopyalandı.
Submitting MPM to TRUBA…|MPM işi TRUBA’ya gönderiliyor…
Preparing MPM compression…|MPM basma analizi hazırlanıyor…
MPM operation stopped.|MPM işlemi durduruldu.
{name} · {points:,} points · peak upper reaction {force:.5g} N|{name} · {points:,} nokta · tepe üst plaka reaksiyonu {force:.5g} N
Maximum platen overlap {overlap:.4g} mm · background cell {cell:.4g} mm|En büyük plaka örtüşmesi {overlap:.4g} mm · arka plan hücresi {cell:.4g} mm
Peridynamic TPMS Compression|TPMS Peridinamik Basma
Brittle fracture in TPMS compression.|TPMS basma altında gevrek kırılma.
TPMS type|TPMS türü
Specimen size / mm|Numune boyutu / mm
TPMS period / mm|TPMS periyodu / mm
Solid fraction / %|Katı oranı / %
Elastic modulus / MPa|Elastisite modülü / MPa
Particles per axis|Eksen başına parçacık
Horizon / spacing|Ufuk / aralık
Critical bond stretch / %|Kritik bağ uzaması / %
Compression / %|Basma / %
Load increments|Yük adımı sayısı
Brittle model · Poisson ratio 0.25 · frictionless contact. Calibrate the critical stretch for the material before comparing loads with experiments.|Gevrek model · Poisson oranı 0,25 · sürtünmesiz temas. Yükleri deneyle karşılaştırmadan önce kritik uzamayı malzeme için kalibre edin.
Fracture methods roadmap|Kırılma yöntemleri planı
Load step|Yük adımı
Result chart|Sonuç grafiği
Force–displacement|Kuvvet–deplasman
Damage by layer|Katmana göre hasar
Mean bond damage / %|Ortalama bağ hasarı / %
Compression displacement / mm|Basma deplasmanı / mm
Top platen force / N|Üst plaka kuvveti / N
Bond damage|Bağ hasarı
Stopped before convergence|Yakınsama sağlanmadan durdu
Unconverged step|Yakınsamayan adım
Ready to run local TPMS compression.|Yerel TPMS basma analizi çalıştırılabilir.
Ready to submit TPMS compression to TRUBA.|TPMS basma işi TRUBA'ya gönderilebilir.
Peridynamic compression requires an analytic TPMS specimen.|Peridinamik basma için analitik TPMS numunesi gerekir.
Use at least eight particle intervals per TPMS period.|TPMS periyodu başına en az sekiz parçacık aralığı kullanın.
● PARTIAL RESULTS|● KISMİ SONUÇ
Peridynamics · PMB|Peridinamik · PMB
Sampling TPMS particles…|TPMS parçacıkları örnekleniyor…
Building peridynamic bonds…|Peridinamik bağlar oluşturuluyor…
Peridynamic results saved.|Peridinamik sonuçlar kaydedildi.
MD Preparation|MD Hazırlama
Saved MD plans|Kayıtlı MD planları
Add package…|Paket ekle…
Open folder|Klasörü aç
Model details|Model ayrıntıları
MD package file|MD paket dosyası
atoms|atom
Prepared atom count|Hazırlanan atom sayısı
Prepared composition|Hazırlanan bileşim
Protocol|Protokol
Prepare a package or add an existing MD package folder.|Bir paket hazırlayın veya mevcut bir MD paket klasörünü ekleyin.
Choose a complete MD package folder.|Tüm gerekli dosyaları içeren bir MD paket klasörü seçin.
Could not open the package folder.|Paket klasörü açılamadı.
Open the folder to read the full file.|Dosyanın tamamını okumak için klasörü açın.
Preparation checks the structure and MEAM mapping. Confirm scientific suitability with a small LAMMPS run and compare against reference data for your alloy.|Hazırlama sırasında yapı ve MEAM eşlemesi kontrol edilir. Bilimsel uygunluğu değerlendirmek için küçük bir LAMMPS koşusu yapın ve sonucu alaşımınızın referans verileriyle karşılaştırın.
BCC single crystal|BCC tek kristal
BCC polycrystal|BCC polikristal
Crystal type|Kristal tipi
Grain count|Tane sayısı
Number of Voronoi grains in the periodic box.|Periyodik kutudaki Voronoi tane sayısı.
Box dimensions|Kutu boyutları
Requested box dimensions|Girilen kutu boyutları
Box length X|Kutu boyutu X
Box length Y|Kutu boyutu Y
Box length Z|Kutu boyutu Z
Estimated atom count|Tahmini atom sayısı
Each box dimension must be at least one lattice parameter.|Her kutu boyutu en az kafes parametresi kadar olmalı.
For a 3D polycrystal, each box dimension must exceed one lattice parameter.|Üç boyutlu polikristal için her kutu boyutu kafes parametresinden büyük olmalı.
The box exceeds the supported atom count.|Kutu boyutları desteklenen atom sayısını aşıyor.
Check the grain count.|Tane sayısını kontrol edin.
Increase the box dimensions or reduce the grain count.|Kutu boyutlarını artırın veya tane sayısını azaltın.
Characteristic grain size|Karakteristik tane boyutu
Atom count and composition counts are estimates until Atomsk builds the grain boundaries.|Atomsk tane sınırlarını oluşturana kadar atom sayıları tahminidir.
How to choose parameters|Parametreler nasıl seçilir?
The lattice parameter a is one BCC cell edge in Å. Use a value for the same alloy and phase. The initial 3.4000 Å is an example.|Kafes parametresi a, BCC birim hücresinin kenarıdır (Å). Aynı alaşım ve faz için belirlenmiş değeri kullanın. Başlangıçtaki 3,4000 Å örnek değerdir.
From the BCC (110) XRD spacing, use a = √2 × d₁₁₀. With MEAM, relax a small periodic single crystal at zero pressure. At the target temperature, average the equilibrated NPT box length and divide by the cell repeats along that axis.|XRD’deki BCC (110) düzlem aralığından a = √2 × d₁₁₀ hesaplanır. MEAM ile küçük bir periyodik tek kristali sıfır basınçta gevşetin. Hedef sıcaklıkta NPT dengelemesi sonrası ortalama kutu uzunluğunu o eksendeki hücre tekrar sayısına bölün.
BCC has two atoms per cell. For a polycrystal, the box volume divided by the grain count gives the mean grain volume. Fix the random seed to reproduce grain positions, orientations and alloy site assignments.|BCC birim hücresinde iki atom vardır. Polikristalde kutu hacminin tane sayısına bölümü ortalama tane hacmini verir. Tane konumlarını, yönelimlerini ve alaşım atom dağılımını tekrarlamak için aynı rastgele tohumu kullanın.
The E20 template uses a fixed 3.9 Å CNA cutoff. Check the cutoff for your lattice parameter before interpreting CNA results.|E20 şablonunun CNA kesim yarıçapı sabit 3,9 Å’dur. CNA sonuçlarını yorumlamadan önce yarıçapın kafes parametrenize uygunluğunu kontrol edin.
Use calculated a|Hesaplanan a’yı kullan
Prepare package|Paketi hazırla
Package prepared at {path}|Paket hazırlandı. {path}
Alloy elements|Elementler
Periodic table|Periyodik tablo
Right-click an element to add it.|Element eklemek için sağ tıklayın.
Composition total|Toplam
Add at least one element.|En az bir element ekleyin.
Target atom count|Hedef atom sayısı
Generated atom count|Üretilecek atom sayısı
Lattice parameter|Kafes parametresi
Temperature|Sıcaklık
Random seed|Rastgele tohum
TRUBA queue|TRUBA kuyruğu
MEAM library|MEAM kütüphanesi
MEAM parameters|MEAM parametreleri
Sources|Kaynaklar
Find the MEAM library and parameters supplied together for your alloy.|Alaşımınız için birlikte yayımlanan MEAM kütüphanesini ve parametre dosyasını bulun.
MEAM library sources|MEAM kütüphanesi kaynakları
MEAM parameter sources|MEAM parametre kaynakları
LAMMPS potential files|LAMMPS potansiyel dosyaları
NIST potentials by element system|Element sistemine göre NIST potansiyelleri
LAMMPS MEAM file guide|LAMMPS MEAM dosya kılavuzu
MEAM element order|MEAM element sırası
Example: Hf Nb Ta Ti Zr|Örnek Hf Nb Ta Ti Zr
Enter the element order used by indices in the MEAM parameter file.|MEAM parametre dosyasındaki indekslerin izlediği element sırasını yazın.
Browse…|Gözat…
MEAM files (*.meam);;All files (*)|MEAM dosyaları (*.meam);;Tüm dosyalar (*)
Preview model|Modeli önizle
Prepare and run on TRUBA|Hazırla ve TRUBA’da çalıştır
BCC supercell|BCC süper hücresi
Box dimensions were rounded to whole BCC unit cells.|Kutu boyutları tam BCC birim hücrelerine yuvarlandı.
Atomsk → Minimization → Equilibration → Tension and CNA|Atomsk → Minimizasyon → Dengeleme → Çekme ve CNA
Choose the MEAM library and parameter files.|MEAM kütüphanesini ve parametre dosyasını seçin.
Generating the structure with Atomsk…|Atomsk ile yapı üretiliyor…
Model prepared. Waiting for the current TRUBA operation…|Model hazır. TRUBA’daki mevcut işlemin tamamlanması bekleniyor…
TRUBA connection changed. Press Prepare and run on TRUBA to submit the prepared model.|TRUBA bağlantısı değişti. Hazır modeli göndermek için Hazırla ve TRUBA’da çalıştır düğmesine basın.
TRUBA is busy. Press Prepare and run on TRUBA again when it is ready.|TRUBA meşgul. Hazır olduğunda Hazırla ve TRUBA’da çalıştır düğmesine tekrar basın.
Submitting the MD job to TRUBA…|MD işi TRUBA’ya gönderiliyor…
Follow the job in TRUBA and open downloaded results from Results.|İşi TRUBA’da izleyin, indirilen çıktıları Sonuçlar penceresinden açın.
Open MD Preparation|MD Hazırlama sekmesini aç
Job settings|İş ayarları
Date modified|Değiştirilme tarihi
Run on|Çalıştırma hedefi
MPI processes|MPI süreçleri
The selected TRUBA queue is unavailable.|Seçilen TRUBA kuyruğu kullanılamıyor.
Local memory limits are available on Windows.|Yerel bellek sınırı Windows’ta kullanılabilir.
LAMMPS executable|LAMMPS programı
Memory (MiB)|Bellek (MiB)
Wall time (HH:MM:SS)|Süre (SS:DD:SS)
LAMMPS module|LAMMPS modülü
Input script…|Girdi betiği…
Job folder…|İş klasörü…
Add Folder|Klasör Ekle
Upload to TRUBA|TRUBA’ya Aktar
Sync|Eşitle
Files|Dosyalar
Root|Kök Klasör
Remove|Çıkar
Reuse|Girdiyi Al
Resume|Sürdür
Download|İndir
Manual|Elle
Choose a job folder outside the ESEN history directory.|ESEN iş geçmişini içermeyen bir iş klasörü seçin.
Load files…|Dosya yükle…
Filter|Filtre
Remove file|Dosyayı çıkar
Include the LAMMPS restart input in the job files.|LAMMPS devam betiğini iş dosyalarına ekleyin.
TRUBA files|TRUBA dosyaları
Scripts|Betikler
Upload|Yükle
Up|Üst klasör
This folder is outside the RustMech project.|Bu klasör RustMech projesinin dışında.
Open file|Dosyayı aç
Load files or drop them on the input list.|Dosyaları yükleyin veya girdi listesine sürükleyip bırakın.
Load existing files. To include a folder, use Job folder.|Mevcut dosyaları yükleyin. Klasör eklemek için İş klasörü düğmesini kullanın.
Connect using the main TRUBA button.|Ana penceredeki TRUBA düğmesinden bağlanın.
Submitting|Gönderiliyor
jobs pending or running|iş kuyrukta veya çalışıyor
not submitted|deney gönderilmedi
Submitted|Gönderildi
Preparing|Hazırlanıyor
Running|Çalışıyor
Queued|Kuyrukta
Interrupted|Kesintiye uğradı
Cancellation requested|İptal istendi
Waiting for Slurm accounting…|Slurm iş kaydı bekleniyor…
Slurm time limit reached. Continuation submitted.|Slurm süre sınırı doldu. Devam işi gönderildi.
Slurm has not confirmed this submission. Refresh to check again.|Slurm gönderimi henüz doğrulamadı. Yeniden kontrol etmek için işi yenileyin.
Connect using the TRUBA account that submitted this job.|Bu işi gönderen TRUBA hesabıyla bağlanın.
Fluid mesh|Akış ağı
Cartesian|Kartezyen
Fitted · OpenFOAM|Yüzeye oturan · OpenFOAM
Surface refinement|Yüzey inceltmesi
Wall layers|Duvar katmanları
Fitted cell budget|Ağ hücresi bütçesi
Parallel analyses|Paralel analizler
Prepare replacement resources near the time limit|Süre dolmadan yedek kaynak hazırla
Maximum simultaneous DoE / analysis queue jobs. CPU cores and RAM are divided equally between jobs; each job has separate results. Limited by the allocated cores and memory. Set 1 for a single large analysis.|Aynı anda çalışan DoE / analiz kuyruğu işi üst sınırı. CPU ve RAM işler arasında eşit bölünür; sonuçlar ayrı kaydedilir. Tahsis edilen çekirdek ve bellekle sınırlıdır. Tek büyük analiz için 1 seçin.
Batch queues request replacement resources in the last hour or the final 10%, whichever is shorter. New analyses wait for them. Running analyses remain subject to the original Slurm time limit.|Toplu analizler, son bir saat ile oturumun son %10'undan hangisi daha kısaysa o aralıkta yedek kaynak ister. Yeni analizler yedek kaynağı bekler. Çalışan analizler mevcut Slurm süre sınırına tabidir.
Inspect geometry (no solve)|Geometriyi incele (çözüm yok)
Geometry diagnostics|Geometri kontrolleri
Geometry inspection saved. No solver files were generated.|Geometri incelemesi kaydedildi. Çözücü dosyası üretilmedi.
Load fluid material...|Akışkan malzemesi yükle...
Load solid material...|Katı malzemesi yükle...
Use Model material|Model malzemesini kullan
Channel · Isothermal CFD|Kanal · İzotermal CFD
Channel section|Kanal kesiti
Cylinder|Silindir
Square / rectangular|Kare / dikdörtgen
Width / mm|Genişlik / mm
Height / mm|Yükseklik / mm
Insert section / mm|Dolgu bölgesi / mm
Specimen X / mm|Numune X / mm
Specimen Y / mm|Numune Y / mm
Specimen Z / mm|Numune Z / mm
Rotation X / °|Dönme X / °
Rotation Y / °|Dönme Y / °
Rotation Z / °|Dönme Z / °
Cells / narrow side|Hücre / kısa kenar
Allow intentional bypass|Numune çevresinde boşluğa izin ver
Import geometry for flow / thermal...|Akış / termal için geometri içe aktar...
Use for flow / thermal|Akış / termal için kullan
Transient water + specimen heat transfer|Zamana bağlı su + numune ısı transferi
Project|Proje
About|Hakkında
About RustMech|RustMech hakkında
Analysis project|Analiz projesi
Open project...|Proje aç...
Save project|Projeyi kaydet
Save project as...|Projeyi farklı kaydet...
Export mechanical 3D results...|Mekanik 3B sonuçları dışa aktar...
Export mechanical 3D results|Mekanik 3B sonuçları dışa aktar
Select saved mechanical result|Kayıtlı mekanik sonucu seç
Select 3D export destination|3B dışa aktarım klasörünü seç
3D results exported to|3B sonuçlar şu klasöre aktarıldı
The selected folder does not contain results_undeformed.vtu.|Seçilen klasörde results_undeformed.vtu bulunamadı.
The selected mechanical result has an empty mesh.|Seçilen mekanik sonucun ağı boş.
The result contains values outside the EnSight Gold numeric range.|Sonuç, EnSight Gold sayı aralığının dışında değerler içeriyor.
The result exceeds the EnSight Gold mesh size limit.|Sonuç, EnSight Gold ağ boyutu sınırını aşıyor.
No case loaded|Yüklenmiş vaka yok
Project inputs restored. Build or open results explicitly.|Proje girdileri geri yüklendi. Ağ üretimini veya sonuç açmayı açıkça başlatın.
Project opened. Use Apply to preview; run analysis explicitly.|Proje açıldı. Önizleme için Apply kullanın; analizi açıkça başlatın.
Sample Settings|Numune Ayarları
Automatic naming|Otomatik isimlendirme
Combination Matrix|Kombinasyon Matrisi
Load selected design|Seçili tasarımı yükle
1  Add parameters|1  Parametre ekle
2  Added parameters|2  Eklenen parametreler
3  Variables|3  Değişkenler
4. Expanded combination matrix|4. Açık kombinasyon matrisi
Source value|Kaynak değer
Remove parameter|Parametreyi kaldır
Vary|Değişken
Value 1|Değer 1
Value 2|Değer 2
Value 3|Değer 3
Add parameters, set fixed values and enter one value per variable cell.|Parametre ekleyin, sabit değerleri ayarlayın ve değişken hücrelerine birer değer girin.
2  Naming sequence|2  İsim sırası
3  Expanded combination matrix|3  Açık kombinasyon matrisi
1  Fixed properties|1  Sabit özellikler
2  Variables|2  Değişkenler
Include in name|İsme ekle
Search parameters...|Parametre ara...
Search this table...|Bu tabloda ara...
Search column|Aranacak sütun
All columns|Tüm sütunlar
Parameter|Parametre
Value|Değer
Name|Ad
+  Add to sequence|+  Sıraya ekle
Block name|Blok adı
Edit the selected block name|Seçili bloğun adını düzenle
Drag blocks to change their order.|Sıralamayı değiştirmek için blokları sürükleyin.
Separator|Ayırıcı
Decimal|Ondalık
Include names|Adları da yaz
Filename preview|Dosya adı önizlemesi
Add parameters above|Yukarıdan parametre ekleyin
Move to variables|Değişkenlere taşı
Add Variable|Değişken ekle
+Column|+Sütun
-Column|-Sütun
Fixed|Sabit
Move to fixed|Sabitlere taşı
Fixed value|Sabit değer
Analysis settings|Analiz ayarları
Analysis Type|Analiz türü
Variable settings|Değişken ayarları
Lower bound|Alt sınır
Upper bound|Üst sınır
Values|Değerler
Material card|Malzeme kartı
+ Value column|+ Değer sütunu
- Last column|- Son sütun
Build matrix|Matrisi oluştur
Add to ML matrix|Makine öğrenmesi matrisine ekle
Sample name|Numune adı
Settings changed; rebuild the matrix.|Ayarlar değişti; matrisi yeniden oluşturun.
Enter fixed properties and one value per variable cell.|Sabit özellikleri girin; değişkenlerin her hücresine bir değer yazın.

STL resolution (mm)|STL çözünürlüğü (mm)
STL surface resolution must be positive and finite (mm).|STL yüzey çözünürlüğü pozitif ve sonlu olmalıdır (mm).
Surface sampling spacing in mm, independent of FE element size. Leave empty or enter auto for automatic resolution. Auto fills in the current automatically calculated value.|SE eleman boyutundan bağımsız yüzey örnekleme aralığı (mm). Otomatik çözünürlük için boş bırakın veya auto yazın. Otomatik düğmesi, hesaplanan güncel değeri alana yazar.
Cancelling resource session; waiting for Slurm to release resources…|Kaynak oturumu iptal ediliyor; Slurm kaynaklarının bırakılması bekleniyor…
Connect with the account that owns this job, or save its credentials to cancel.|İptal için işin sahibi olan hesapla bağlanın veya o hesabın giriş bilgilerini kaydedin.
Saved job states · Cancel uses the connected or saved owning account.|Kayıtlı iş durumları · İptal, bağlı veya kayıtlı iş sahibi hesabını kullanır.
Cancelling remote job; waiting for confirmation…|Uzak iş iptal ediliyor; sunucu teyidi bekleniyor…
Cancellation failed: authentication rejected. Reconnect with valid credentials and retry.|İptal başarısız: giriş bilgileri reddedildi. Geçerli bilgilerle yeniden bağlanıp tekrar deneyin.
Cancellation failed: connection lost. Check VPN, reconnect and retry.|İptal başarısız: bağlantı kesildi. VPN'i kontrol edin, yeniden bağlanıp tekrar deneyin.
Cancellation not confirmed. The saved job is kept; reconnect and retry.|İptal doğrulanamadı. İş kaydı korundu; yeniden bağlanıp tekrar deneyin.
Remote job stopped; removed from pending jobs.|Uzak iş durdu; bekleyen işler listesinden kaldırıldı.
Operation progress|İşlem ilerlemesi
Percentage of completed analysis stages|Tamamlanan analiz aşamalarının yüzdesi
Reading imported geometry…|İçe aktarılan geometri okunuyor…
Saving imported geometry…|İçe aktarılan geometri kaydediliyor…
Generating finite element mesh…|Sonlu eleman meshi oluşturuluyor…
Preparing operation…|İşlem hazırlanıyor…
Validating analysis settings and mesh…|Analiz ayarları ve mesh doğrulanıyor…
Exporting geometry and mesh…|Geometri ve mesh dışa aktarılıyor…
Extracting reactions and response curves…|Tepkiler ve yanıt eğrileri çıkarılıyor…
Exporting graphs…|Grafikler dışa aktarılıyor…
Exporting result fields…|Sonuç alanları dışa aktarılıyor…
Building fluid mesh…|Akışkan meshi oluşturuluyor…
Exporting OpenFOAM mesh and case files…|OpenFOAM mesh ve analiz dosyaları dışa aktarılıyor…
Checking OpenFOAM runtime…|OpenFOAM çalışma ortamı kontrol ediliyor…
Checking mesh and solving flow…|Mesh kontrol ediliyor ve akış çözülüyor…
Processing flow results…|Akış sonuçları işleniyor…
Building water and solid region meshes…|Su ve katı bölgelerin meshleri oluşturuluyor…
Exporting thermal regions and case files…|Isıl bölgeler ve analiz dosyaları dışa aktarılıyor…
Checking thermal solver runtime…|Isıl çözücü çalışma ortamı kontrol ediliyor…
Splitting regions, checking mesh and solving heat transfer…|Bölgeler ayrılıyor, mesh kontrol ediliyor ve ısı transferi çözülüyor…
Processing temperature history and thermal results…|Sıcaklık geçmişi ve ısıl sonuçlar işleniyor…
Checking TRUBA resource limits…|TRUBA kaynak sınırları kontrol ediliyor…
Finding planar faces and validating geometry…|Düzlemsel yüzeyler bulunuyor ve geometri doğrulanıyor…
Language|Dil
Interface language|Arayüz dili
Settings|Ayarlar
Setup|Kurulum
Geometry|Geometri
Structure|Yapı
Structure Family|Yapı ailesi
Structure family|Yapı ailesi
Structure type|Yapı türü
Type|Tür
Structure / Dimensions|Yapı / Ölçüler
Preview structure|Yapıyı önizle
Preview mesh|Ağı önizle
Structure preview|Yapı önizlemesi
The preview uses the first configured level for each variable.|Önizleme her değişken için tanımlanan ilk seviyeyi kullanır.
Building preview…|Önizleme oluşturuluyor…
No geometry to display.|Görüntülenecek geometri yok.
Cartesian grid|Kartezyen ağ
Cartesian precheck|Kartezyen ön kontrol
Cells|Hücreler
Boundary layers|Sınır katmanları
Solid voxels|Katı vokseller
Particles|Parçacıklar
Family|Aile
Morphology|Morfoloji
Dimensions|Ölçüler
Size|Boyut
Cube size|Küp boyutu
Cube size (mm)|Küp boyutu (mm)
Cell size|Hücre boyutu
Cell Size (mm)|Hücre boyutu (mm)
Cell size (mm)|Hücre boyutu (mm)
Cell / Pore (mm)|Hücre / Gözenek (mm)
Density|Yoğunluk
Density (%)|Yoğunluk (%)
Target Density (%)|Hedef yoğunluk (%)
Actual Density (%)|Gerçek yoğunluk (%)
Density / g·cm⁻³|Yoğunluk / g·cm⁻³
Density / kg/m³|Yoğunluk / kg/m³
Random seed|Rastgelelik tohumu
Seed|Tohum
Material|Malzeme
Custom material|Özel malzeme
Material name|Malzeme adı
Constitutive law|Malzeme modeli
Linear elastic|Doğrusal elastik
Bilinear elastoplastic|Çift doğrulu elastoplastik
Tabulated isotropic hardening|Tablolu izotropik pekleşme
Young's modulus|Young modülü
Poisson's ratio|Poisson oranı
True yield stress|Gerçek akma gerilmesi
Tangent modulus Eₜ|Teğet modülü Eₜ
True Stress–Plastic Strain Curve|Gerçek gerilme–plastik şekil değiştirme eğrisi
True stress–strain (MISO)|Gerçek gerilme–şekil değiştirme (MISO)
Stress unit|Gerilme birimi
True stress / MPa|Gerçek gerilme / MPa
Plastic strain|Plastik şekil değiştirme
Load material file|Malzeme dosyası yükle
Save material|Malzemeyi kaydet
Delete material|Malzemeyi sil
Enable editing of material properties|Malzeme özelliklerini düzenlemeyi aç
Tangent modulus guide and approximate datasheet calculator|Teğet modülü kılavuzu ve yaklaşık katalog hesabı
Mesh|Ağ
FE Mesh|SE ağı
FE mesh|SE ağı
Mesh diagnostics|Ağ tanılama
Mesh Convergence|Ağ yakınsaması
Mesh / analysis settings|Ağ / Analiz ayarları
Local mesh size (mm)|Yerel ağ boyutu (mm)
Mesh Size (mm)|Ağ boyutu (mm)
Mesh Size|Ağ boyutu
Global size|Genel boyut
Local size|Yerel boyut
Local Size|Yerel boyut
Element type|Eleman türü
Element|Eleman
Elements|Elemanlar
FE Elements|SE elemanları
Nodes|Düğümler
Tetra mesher|Tetra ağ oluşturucu
Automatic mesh selection|Otomatik ağ seçimi
Run only the starting mesh on TRUBA|TRUBA'da yalnızca başlangıç ağını çalıştır
Use the job's full RAM budget for one mesh without refining it.|İşin tüm RAM bütçesini tek ağ seviyesi için kullanır.
Retry this mesh with more RAM|Bu ağı daha fazla RAM ile yeniden dene
Insufficient RAM|Yetersiz RAM
Single mesh completed.|Tek ağ analizi tamamlandı.
Insufficient RAM. Further refinement stopped. Right-click the affected row to retry with more RAM.|Yetersiz RAM. Ağ inceltme durduruldu. Daha fazla RAM ile yeniden denemek için ilgili satıra sağ tıklayın.
Automatic|Otomatik
Auto|Otomatik
Auto (TPMS cells / Netgen)|Otomatik (TPMS hücreleri / Netgen)
Voxel hexahedral (HEX20)|Voksel heksahedral (HEX20)
Manual global sizes|Elle girilen genel boyutlar
Starting mesh|Başlangıç ağı
Starting size|Başlangıç boyutu
Starting local size|Başlangıç yerel boyutu
Refinement|İnceltme
Refinement factor|İnceltme katsayısı
Tolerance|Tolerans
Absolute tolerance|Mutlak tolerans
Relative tolerance|Bağıl tolerans
Minimum global size|En küçük genel boyut
Maximum elements|En fazla eleman
Maximum attempts|En fazla deneme
Study time budget|Çalışma süresi sınırı
Reference level|Referans seviye
Use this mesh|Bu ağı kullan
Run mesh convergence|Ağ yakınsamasını çalıştır
Export mesh convergence|Ağ yakınsamasını dışa aktar
Add 2 Extra|2 seviye daha ekle
Previously submitted TRUBA studies with running or queued mesh levels|Çalışan veya kuyrukta bekleyen ağ seviyeleri olan TRUBA çalışmaları
New study|Yeni çalışma
Submit the next two levels from this study while the current jobs run.|Mevcut işler sürerken bu çalışmanın sonraki iki ağ seviyesini gönderir.
Saved TRUBA jobs · connect to refresh their status|Kayıtlı TRUBA işleri · durumlarını güncellemek için bağlanın
TRUBA tracking paused|TRUBA takibi duraklatıldı
TRUBA tracking paused · select the saved study or reconnect to resume|TRUBA takibi duraklatıldı · sürdürmek için kayıtlı çalışmayı seçin veya yeniden bağlanın
Saved TRUBA mesh levels restored.|Kayıtlı TRUBA ağ seviyeleri geri yüklendi.
No running or queued TRUBA mesh levels in this study.|Bu çalışmada çalışan veya kuyrukta bekleyen TRUBA ağ seviyesi yok.
Solver|Çözücü
Solver engine|Çözücü motoru
Solver and loading|Çözücü ve yükleme
Solver Not Configured|Çözücü ayarlanmadı
Loading|Yükleme
Loading mode|Yükleme türü
Displacement control|Yer değiştirme kontrolü
Force control|Kuvvet kontrolü
Displacement|Yer değiştirme
Displacement (mm)|Yer değiştirme (mm)
Displ. (mm)|Yer değiştirme (mm)
Displacement / mm|Yer değiştirme / mm
Latest mechanical result · Final load step|Son mekanik analiz sonucu · Son yük adımı
Result field|Sonuç alanı
Model preview|Model önizlemesi
von Mises stress / MPa|von Mises gerilmesi / MPa
Compression|Basma
General geometry|Genel geometri
Compression (mm)|Basma (mm)
Compression / mm|Basma / mm
Compression force (N)|Basma kuvveti (N)
Compression force / N|Basma kuvveti / N
Actuator travel|Aktüatör hareketi
Actuator travel (mm)|Aktüatör hareketi (mm)
Final strain (%)|Son şekil değiştirme (%)
Final nominal strain (%)|Son nominal şekil değiştirme (%)
Target strain (%)|Hedef şekil değiştirme (%)
Substeps|Alt adımlar
Initial substeps|Başlangıç alt adımları
Minimum substeps|En az alt adım
Maximum substeps|En fazla alt adım
Automatic defaults|Otomatik varsayılanlar
Fixed increments|Sabit artımlar
Contact and friction|Temas ve sürtünme
Contact algorithm|Temas algoritması
Contact method|Temas yöntemi
Bottom contact|Alt yüzey teması
Top contact|Üst yüzey teması
Bottom platen friction μ|Alt plaka sürtünmesi μ
Top platen friction μ|Üst plaka sürtünmesi μ
Self-contact|Öz temas
Self-contact friction μ|Öz temas sürtünmesi μ
Frictional (Coulomb)|Sürtünmeli (Coulomb)
Frictionless|Sürtünmesiz
Bonded (initial contact)|Bağlı (ilk temas)
Rough (no sliding, separation allowed)|Pürüzlü (kayma yok, ayrılabilir)
Rough self-contact (no sliding)|Pürüzlü öz temas (kayma yok)
Disabled (no self-contact)|Kapalı (öz temas yok)
Augmented Lagrangian (Default)|Artırılmış Lagrange (varsayılan)
Pure Penalty|Saf ceza
Penalty factor|Ceza katsayısı
Contact damping (FDMN)|Temas sönümü (FDMN)
Normal stiffness factor|Normal rijitlik katsayısı
Penetration tolerance|Nüfuziyet toleransı
Line search|Çizgi araması
Standard test|Standart deney
Test profile|Deney profili
Specimen height|Numune yüksekliği
Specimen architecture|Numune mimarisi
Porous throughout|Tamamen gözenekli
Porous centre / solid ends|Gözenekli orta / dolu uçlar
Porous centre length|Gözenekli orta uzunluğu
Özel deney / Custom experiment|Özel deney
Nonlinear Compression|Doğrusal olmayan basma
Finite Element|Sonlu Eleman
Mechanic|Mekanik
Experiments (DoE)|Deneyler (DoE)
Experiments|Deneyler
Flow CFD|Akış CFD
Flow (CFD)|Akış (CFD)
Thermal CHT|Isıl CHT
Thermal (CHT)|Isıl (CHT)
Transient Heat Transfer|Geçici ısı transferi
Designs|Tasarımlar
Design|Tasarım
Design Pool|Tasarım havuzu
Design Pool (Gitterburg)...|Tasarım havuzu (Gitterburg)...
Design Pool - Gitterburg / RustMech|Tasarım havuzu - Gitterburg / RustMech
Design Pool and analysis queue|Tasarım havuzu ve analiz kuyruğu
Analysis queue|Analiz kuyruğu
Analysis queue...|Analiz kuyruğu...
Analysis Queue|Analiz Kuyruğu
Add to Queue|Kuyruğa Ekle
Execution target|Yürütme hedefi
Install local Kratos from the Kratos settings before running this analysis.|Bu analizi çalıştırmadan önce Kratos ayarlarından yerel Kratos'u kurun.
Install local Kratos|Yerel Kratos'u Kur
Installing and checking Kratos…|Kratos kuruluyor ve doğrulanıyor…
Local Kratos installation is available on Windows.|Yerel Kratos Windows'ta kurulabilir.
Local Kratos requires 64-bit Python 3.14 and an internet connection for setup.|Yerel Kratos kurulumu için 64 bit Python 3.14 ve internet bağlantısı gerekir.
Ready to submit analysis to TRUBA.|Analiz TRUBA'ya gönderilmeye hazır.
Local|Yerel
Stop the analysis queue before removing jobs.|İşleri çıkarmadan önce analiz kuyruğunu durdurun.
Analysis queue - RustMech|Analiz kuyruğu - RustMech
Add current model to analysis queue|Mevcut modeli analiz kuyruğuna ekle
Configure selected...|Seçileni yapılandır...
1. New geometry|1. Yeni geometri
2. Saved designs|2. Kaydedilen tasarımlar
Saved designs|Kaydedilen tasarımlar
Check the designs to include in the experiment matrix.|Deney matrisine eklenecek tasarımları işaretleyin.
3. Selected design / variables|3. Seçili tasarım / Değişkenler
Experiment matrix|Deney matrisi
Experiment variables|Deney değişkenleri
Machine learning|Makine öğrenmesi
Design · Size / Cell / Density|Tasarım · Boyut / Hücre / Yoğunluk
Vary|Değişken
Min|Min
Max|Maks
Levels|Seviye
Sampling|Örnekleme
LHS parameters|LHS parametreleri
LHS samples per structure|Yapı başına LHS örneği
Generate experiments|Deneyleri oluştur
Generate ML matrix|ML matrisi oluştur
Preview in 3D|3B önizle
Remove selected|Seçileni kaldır
Export CSV|CSV dışa aktar
CSV exported.|CSV dosyası dışa aktarıldı.
CSV export failed|CSV dışa aktarılamadı
Choose a different file from the saved experiment matrix.|Kayıtlı deney matrisinden farklı bir dosya seçin.
Check meshes|Ağları denetle
Run batch (ANSYS)|Toplu çalıştır (ANSYS)
Exp ID|Deney no
Run|Çalıştır
Mass (g)|Kütle (g)
Status|Durum
Max Force (N)|En yüksek kuvvet (N)
Max von Mises (MPa)|En yüksek von Mises (MPa)
Effective E* (MPa)|Etkin E* (MPa)
Effective modulus (MPa)|Etkin modül (MPa)
Run folder|Çalışma klasörü
Results|Sonuçlar
Browse saved analysis results|Kayıtlı analiz sonuçlarını görüntüle
Run name|Çalışma adı
No saved analysis results found.|Kayıtlı analiz sonucu bulunamadı.
Cannot open saved result: {path}|Kayıtlı sonuç açılamadı: {path}
Saved mechanical result · Final load step|Kayıtlı mekanik sonuç · Son yük adımı
Result|Sonuç
Force|Kuvvet
Force (N)|Kuvvet (N)
Final force (N)|Son kuvvet (N)
Force difference (%)|Kuvvet farkı (%)
Displacement difference (%)|Yer değiştirme farkı (%)
SEA difference (%)|SEA farkı (%)
External work (J)|Dış iş (J)
External compression work (J)|Dış basma işi (J)
Load|Yükle
Open|Aç
Save|Kaydet
Apply|Uygula
Add|Ekle
Edit|Düzenle
Delete|Sil
Close|Kapat
Cancel|İptal
Stop|Durdur
Refresh|Yenile
Help|Yardım
View|Görünüm
Reset view|Görünümü sıfırla
Update preview|Önizlemeyi güncelle
Run Analysis|Analizi çalıştır
Choose where to save analysis results|Analiz sonuçlarının kaydedileceği klasörü seçin
Select an empty folder or create a new folder for this analysis.|Bu analiz için boş bir klasör seçin veya yeni bir klasör oluşturun.
Run analysis queue|Analiz kuyruğunu çalıştır
Forced Stop|Zorla durdur
Cancel meshing|Ağı iptal et
Use geometry|Geometriyi kullan
Use generated structure|Üretilen yapıyı kullan
Import geometry…|Geometri içe aktar…
Geometry import|Geometri içe aktarma
No imported geometry|İçe aktarılan geometri yok
Select support face|Mesnet yüzeyini seç
Select compression face|Basma yüzeyini seç
Selecting support face|Mesnet yüzeyi seçiliyor
Selecting support face (right-click)|Mesnet yüzeyi seçiliyor (sağ tıklayın)
Selecting compression face (right-click)|Basma yüzeyi seçiliyor (sağ tıklayın)
Show platens|Plakaları göster
Orthographic|Ortografik
Perspective|Perspektif
Smooth geometry|Pürüzsüz geometri
Smooth + FE overlay|Geometri + SE üst üste
Overlay|Üst üste
Top|Üst
Bottom|Alt
Advanced|Gelişmiş
TRUBA connection|TRUBA bağlantısı
Remote compute|Uzak hesaplama
Connection|Bağlantı
Resources|Kaynaklar
Server and VPN|Sunucu ve VPN
Credentials and password storage|Kimlik bilgileri ve parola
Connection settings|Bağlantı ayarları
CPU, RAM and resource session|CPU, RAM ve kaynak oturumu
Advanced · remote runtime|Gelişmiş · Uzak çalışma ortamı
VPN required|VPN gerekli
Username|Kullanıcı adı
TRUBA username|TRUBA kullanıcı adı
Password|Parola
Show password|Parolayı göster
Remember password for this Windows user|Bu Windows kullanıcısı için parolayı hatırla
Connect|Bağlan
Connected|Bağlı
Working|Çalışıyor
Disconnect|Bağlantıyı kes
Disconnected|Bağlı değil
Connecting...|Bağlanıyor...
Connected to TRUBA|TRUBA bağlantısı kuruldu
Disconnecting...|Bağlantı kesiliyor...
Enter your TRUBA username and password.|TRUBA kullanıcı adınızı ve parolanızı girin.
Connected to TRUBA. Password could not be saved for this Windows user.|TRUBA bağlantısı kuruldu. Parola bu Windows kullanıcısı için kaydedilemedi.
Saved credentials could not be removed.|Kayıtlı kimlik bilgileri silinemedi.
Saved credentials cannot be read by this Windows user. Enter your credentials again.|Bu Windows kullanıcısı kayıtlı kimlik bilgilerini okuyamıyor. Bilgilerinizi yeniden girin.
Saved credentials belong to this Windows account. Use separate Windows accounts on shared computers.|Kayıtlı kimlik bilgileri bu Windows hesabına aittir. Ortak bilgisayarlarda ayrı Windows hesapları kullanın.
Verify TRUBA server identity|TRUBA sunucu kimliğini doğrulayın
First connection to this server. Verify this fingerprint with TRUBA before accepting:|Bu sunucuya ilk bağlantı. Kabul etmeden önce parmak izini TRUBA ile doğrulayın:
SSH server reached. Waiting for you to verify the server fingerprint in the confirmation window.|SSH sunucusuna ulaşıldı. Onay penceresinde sunucu parmak izini doğrulamanız bekleniyor.
Server identity verified. Checking username and password...|Sunucu kimliği doğrulandı. Kullanıcı adı ve parola denetleniyor...
Server identity was not approved. Click Connect to review the fingerprint again.|Sunucu kimliği onaylanmadı. Parmak izini yeniden incelemek için Bağlan'a tıklayın.
This server does not accept password login for this account. Check the login method used in WinSCP.|Sunucu bu hesap için parola ile girişi kabul etmiyor. WinSCP giriş yöntemini kontrol edin.
SSH connection timed out. Check VPN and retry; the server may be busy.|SSH bağlantısı zaman aşımına uğradı. VPN'i kontrol edip yeniden deneyin; sunucu meşgul olabilir.
Connection lost; waiting for VPN / reconnecting...|Bağlantı kesildi; VPN bekleniyor / yeniden bağlanılıyor...
Waiting for VPN / TRUBA network access...|VPN / TRUBA ağ erişimi bekleniyor...
Username or password was rejected. Check your credentials and click Connect.|Kullanıcı adı veya parola reddedildi. Bilgileri kontrol edip Bağlan'a tıklayın.
Server identity changed. Connection refused; contact TRUBA support.|Sunucu kimliği değişti. Bağlantı reddedildi; TRUBA desteğiyle iletişime geçin.
SSH connection could not be established. Check the server identity and VPN, then retry.|SSH bağlantısı kurulamadı. Sunucu kimliğini ve VPN'i kontrol edip yeniden deneyin.
Local connection settings could not be read or written.|Yerel bağlantı ayarları okunamadı veya yazılamadı.
Run mesh generation, convergence and analyses on TRUBA|Ağ üretimini, yakınsamayı ve analizleri TRUBA'da çalıştır
Auto · program chooses resources|Otomatik · Kaynakları program seçer
Queue|Kuyruk
Account|Hesap
Wall time|Oturum süresi
Time limit|Süre sınırı
CPU cores|CPU çekirdeği
GPUs|GPU sayısı
RAM / GiB|RAM / GiB
Runtime Python|Çalışma ortamı Python'u
Python module|Python modülü
OpenCFD module|OpenCFD modülü
WinSCP site (optional)|WinSCP kaydı (isteğe bağlı)
WinSCP site|WinSCP kaydı
WinSCP saved site|WinSCP kayıtlı bağlantısı
Automatic if only one TRUBA site|Tek TRUBA kaydı varsa otomatik
Allocate resources|Kaynak ayır
Refresh session|Oturumu yenile
Release resources|Kaynakları bırak
Refresh queue limits|Kuyruk sınırlarını yenile
Check remote capabilities|Uzak yetenekleri denetle
Prepare remote runtime|Uzak ortamı hazırla
Retrieve remote job…|Uzak işi geri al…
Retrieve TRUBA job…|TRUBA işini geri al…
Stop remote check/setup|Uzak denetimi / kurulumu durdur
Local mode. Connecting alone does not move computations to TRUBA.|Yerel mod. Bağlanmak tek başına hesaplamaları TRUBA'ya taşımaz.
Local mode. Remote jobs already submitted can be retrieved using their records.|Yerel mod. Gönderilmiş uzak işler kayıtlarıyla geri alınabilir.
No resource session. Wall time includes idle time and limits all jobs in the allocation.|Kaynak oturumu yok. Oturum süresi boşta geçen süreyi de kapsar ve tahsisteki tüm işleri sınırlar.
Slurm stops running analyses when the allocation reaches its wall time.|Tahsis süresi dolduğunda Slurm çalışan analizleri durdurur.
Queue unavailable. Connect and refresh limits or choose another queue.|Kuyruk kullanılamıyor. Bağlanıp sınırları yenileyin veya başka kuyruk seçin.
Min: unavailable|Min: bilinmiyor
Max: unavailable|Maks: bilinmiyor
No site minimum RAM is published. Enter a positive amount; the maximum depends on CPU count. Slurm defaults are not minimum requirements.|Asgari RAM değeri yayımlanmamış. Pozitif bir değer girin; üst sınır CPU sayısına bağlıdır. Slurm varsayılanları asgari gereksinim değildir.
TRUBA reference · refresh to verify|TRUBA referansı · Doğrulamak için yenileyin
Reading queue and memory limits from Slurm…|Slurm kuyruk ve bellek sınırları okunuyor…
Configuration changed. Check remote capabilities before computing.|Ayarlar değişti. Hesaplamadan önce uzak yetenekleri denetleyin.
Connection changed. Check remote capabilities again; local fallback is disabled.|Bağlantı değişti. Uzak yetenekleri yeniden denetleyin; yerel yedeğe geçiş kapalı.
Resource request submitted. Waiting for Slurm; runtime will be verified when ready.|Kaynak isteği gönderildi. Slurm bekleniyor; hazır olduğunda çalışma ortamı doğrulanacak.
Cancelling the remote check/setup job…|Uzak denetim / kurulum işi iptal ediliyor…
TRUBA ready. Click Apply to generate the geometry remotely.|TRUBA hazır. Geometriyi uzakta üretmek için Uygula'ya tıklayın.
TRUBA · remote compute|TRUBA · Uzak hesaplama
TRUBA · saving work for reconnection…|TRUBA · Yeniden bağlantı için işler kaydediliyor…
Saving TRUBA work before closing…|Kapatmadan önce TRUBA işleri kaydediliyor…
Finish or stop current computations before changing execution mode.|Çalıştırma modunu değiştirmeden önce hesaplamaları bitirin veya durdurun.
Finish or stop current computations before changing the resource session.|Kaynak oturumunu değiştirmeden önce hesaplamaları bitirin veya durdurun.
Finish or stop current computations before refreshing the remote runtime.|Uzak ortamı yenilemeden önce hesaplamaları bitirin veya durdurun.
Finish or stop the remote mesh job before modifying its runtime.|Çalışma ortamını değiştirmeden önce uzak ağ işini bitirin veya durdurun.
Wait for the current TRUBA resource operation to finish, then retry.|Devam eden TRUBA kaynak işlemi bitince tekrar deneyin.
Allocate resources and wait for a ready session before checking or preparing the runtime.|Çalışma ortamını denetlemek veya hazırlamak için kaynak ayırın ve oturum hazır olana kadar bekleyin.
Refresh the resource session and wait until it is ready before computing.|Hesaplamadan önce kaynak oturumunu yenileyip hazır olmasını bekleyin.
Closing: stopping remote work and releasing the resource session…|Kapanıyor: Uzak işler durdurulup kaynak oturumu bırakılıyor…
Select a supported CPU queue or Auto before allocating resources.|Kaynak ayırmadan önce desteklenen bir CPU kuyruğu veya Otomatik seçin.
TRUBA computation mode is not enabled.|TRUBA hesaplama modu açık değil.
Ready|Hazır
Pending|Bekliyor
Checking|Denetleniyor
Checking...|Denetleniyor...
Solving|Çözülüyor
Solving (ANSYS)...|Çözülüyor (ANSYS)...
Completed|Tamamlandı
Failed|Başarısız
Cancelled|İptal edildi
Cancelled (Forced Stop)|İptal edildi (zorla durdur)
Converged|Yakınsadı
Mesh Ready|Ağ hazır
Mesh Disconnected|Ağ bağlantısız
Analysis running...|Analiz sürüyor...
Analysis cancelled. Analysis and solver processes forcibly terminated.|Analiz iptal edildi. Analiz ve çözücü süreçleri zorla sonlandırıldı.
Building FE mesh in background…|SE ağı arka planda oluşturuluyor…
Updating geometry…|Geometri güncelleniyor…
Generating geometry in background...|Geometri arka planda üretiliyor...
Preparing geometry preview...|Geometri önizlemesi hazırlanıyor...
Preparing geometry preview…|Geometri önizlemesi hazırlanıyor…
The selected analysis method is not available.|Seçilen analiz yöntemi kullanılamıyor.
Allocate or refresh the resource session and wait until it is ready before computing.|Kaynak oturumunu ayırın veya yenileyin ve hesaplamadan önce hazır olmasını bekleyin.
TRUBA runtime is being checked and prepared automatically. Wait until READY before computing.|TRUBA çalışma ortamı otomatik olarak denetleniyor ve hazırlanıyor. Hesaplamadan önce hazır olmasını bekleyin.
TRUBA runtime preparation did not complete.|TRUBA çalışma ortamının hazırlığı tamamlanmadı.
Settings changed — updating geometry preview...|Ayarlar değişti — Geometri önizlemesi güncelleniyor...
Cancelling mesh generation…|Ağ üretimi iptal ediliyor…
Ready · uses loading and contact conditions from the main setup|Hazır · Ana ayarlardaki yükleme ve temas koşullarını kullanır
Geometry or mesh settings have changed. Click Apply to update the preview.|Geometri veya ağ ayarları değişti. Önizlemeyi güncellemek için Uygula'ya tıklayın.
Structure or dimensions have changed. Click Apply to update the preview.|Yapı veya ölçüler değişti. Önizlemeyi güncellemek için Uygula'ya tıklayın.
Export to ANSYS|ANSYS'e Aktar
Save the generated FE mesh in an ANSYS-compatible format.|Oluşturulan sonlu eleman ağını ANSYS uyumlu bir biçimde kaydet.
ANSYS mesh (*.cdb)|ANSYS ağı (*.cdb)
Abaqus input (*.inp)|Abaqus girdi dosyası (*.inp)
Nastran bulk data (*.bdf)|Nastran veri dosyası (*.bdf)
Nastran bulk data (*.dat)|Nastran veri dosyası (*.dat)
Nastran bulk data (*.nas)|Nastran veri dosyası (*.nas)
Fluent mesh (*.msh)|Fluent ağı (*.msh)
LS-DYNA keyword (*.k)|LS-DYNA girdi dosyası (*.k)
LS-DYNA keyword (*.key)|LS-DYNA girdi dosyası (*.key)
Voxel-derived solid elements for mesh export.|Dışa aktarma için vokselden türetilen katı elemanlar.
Mixed hex and pyramid cells for mesh export.|Dışa aktarma için karma altıyüzlü ve piramit elemanlar.
Exporting mesh to ANSYS…|Ağ ANSYS'e aktarılıyor…
ANSYS mesh export failed|ANSYS ağı dışa aktarılamadı
ANSYS mesh saved to|ANSYS ağı kaydedildi
Geometry preview unavailable.|Geometri önizlemesi kullanılamıyor.
Geometry is empty; adjust its parameters.|Geometri boş; parametreleri düzenleyin.
Dimensions changed. Click Apply to generate geometry.|Ölçüler değişti. Geometriyi üretmek için Uygula'ya tıklayın.
Generate the geometry preview using the entered structure and dimensions.|Girilen yapı ve ölçülerle geometri önizlemesini oluşturun.
No Pending Rows|Bekleyen satır yok
All checked rows are already marked Completed, or no rows are checked.|İşaretli tüm satırlar tamamlanmış veya hiçbir satır işaretlenmemiş.
No flow case yet|Henüz akış senaryosu yok
Build channel / export case|Kanalı oluştur / Senaryoyu dışa aktar
Run flow analysis|Akış analizini çalıştır
Stop flow analysis|Akış analizini durdur
Build thermal regions / export case|Isıl bölgeleri oluştur / Senaryoyu dışa aktar
Run transient heat transfer|Geçici ısı transferini çalıştır
Stop heat transfer analysis|Isı transferi analizini durdur
Open saved thermal case|Kayıtlı ısıl senaryoyu aç
Open result folder|Sonuç klasörünü aç
View latest results|Son sonuçları göster
View latest results. Right-click to open the result folder.|Son sonuçları göster. Sonuç klasörünü açmak için sağ tıklayın.
No completed analysis results found.|Tamamlanmış analiz sonucu bulunamadı.
Cylindrical channel · Isothermal CFD|Silindirik kanal · İzotermal CFD
Transient water + TPMS heat transfer|Geçici su + TPMS ısı transferi
Flow model|Akış modeli
Laminar|Laminer
Turbulent · k–ω SST|Türbülanslı · k–ω SST
Inlet flow / mL/min|Giriş debisi / mL/dk
Inlet velocity / m/s|Giriş hızı / m/s
Diameter / mm|Çap / mm
Cells / diameter|Çap başına hücre
SIMPLE iterations|SIMPLE yinelemeleri
WSL distribution|WSL dağıtımı
OpenFOAM environment|OpenFOAM ortamı
Viscosity / Pa·s|Viskozite / Pa·s
Pressure / Pa|Basınç / Pa
Gauge pressure / Pa|Gösterge basıncı / Pa
Speed / m/s|Hız / m/s
Temperature / °C|Sıcaklık / °C
Pressure · central slice|Basınç · Orta kesit
Pressure · volume cutaway|Basınç · Hacim kesiti
Speed · central slice|Hız · Orta kesit
Speed · volume cutaway|Hız · Hacim kesiti
Streamlines|Akım çizgileri
Fluid domain cutaway|Akışkan bölgesi kesiti
Solid specimen|Katı numune
Specimen interior|Numune içi
Empty pipe reference|Boş boru referansı
Empty pipe|Boş boru
Duration / s|Süre / s
Save interval / s|Kayıt aralığı / s
Maximum time step / s|En büyük zaman adımı / s
Coupling correctors|Bağlaşım düzelticileri
CHT coupling iterations|CHT bağlaşım yinelemeleri
Water ρ / kg/m³|Su ρ / kg/m³
Water cp / J/(kg·K)|Su cp / J/(kg·K)
Water k / W/(m·K)|Su k / W/(m·K)
Solid ρ / kg/m³|Katı ρ / kg/m³
Solid cp / J/(kg·K)|Katı cp / J/(kg·K)
Solid k / W/(m·K)|Katı k / W/(m·K)
Water + TPMS temperatures|Su + TPMS sıcaklıkları
Water temperature · slice|Su sıcaklığı · Kesit
Water pressure · slice|Su basıncı · Kesit
Water speed · slice|Su hızı · Kesit
TPMS temperature · cutaway|TPMS sıcaklığı · Kesit
Water mean|Ortalama su sıcaklığı
TPMS mean|Ortalama TPMS sıcaklığı
TPMS min–max|TPMS min–maks
Play|Oynat
Pause|Duraklat
Time|Zaman
Time / s|Zaman / s
Time =|Zaman =
Seconds (s)|Saniye (s)
Centiseconds (cs)|Santisaniye (cs)
Minutes (min)|Dakika (dk)
Hours (h)|Saat (sa)
Bottom|Alt
Top|Üst
Unknown|Bilinmiyor
Unlimited|Sınırsız
Yes|Evet
No|Hayır
OK|Tamam
Discard|Vazgeç
Apply current settings and build the FE mesh.|Mevcut ayarları uygulayıp SE ağını oluşturun.
Apply geometry settings and refresh the 3D preview.|Geometri ayarlarını uygulayıp 3B önizlemeyi yenileyin.
Apply the selected structure and geometry settings to the 3D preview.|Seçili yapı ve geometri ayarlarını 3B önizlemeye uygulayın.
Save settings and start the selected solver.|Ayarları kaydedip seçili çözücüyü başlatın.
Connect to TRUBA with your own username and password|Kendi kullanıcı adınız ve parolanızla TRUBA'ya bağlanın
Connect to TRUBA with saved credentials|Kayıtlı bilgilerle TRUBA’ya bağlan
TRUBA Desktop session|TRUBA masaüstü oturumu
Open TRUBA|TRUBA’yı aç
Shared TRUBA ARF session · {username}|Paylaşılan TRUBA ARF oturumu · {username}
Connect to TRUBA ARF in the TRUBA application first.|Önce TRUBA uygulamasında ARF’ye bağlanın.
RustMech analyses use the TRUBA ARF cluster.|RustMech analizleri TRUBA ARF kümesini kullanır.
View RustMech analysis files and synchronized results|RustMech analiz dosyalarını ve eşitlenen sonuçları görüntüle
Resources…|Kaynaklar…
Compute resources|Hesaplama kaynakları
Local inputs|Yerel girdiler
Analysis|Analiz
TRUBA files|TRUBA dosyaları
Local results|Yerel sonuçlar
RustMech analyses|RustMech analizleri
Track jobs in TRUBA WORKS.|İşleri TRUBA’nın WORKS sekmesinden takip edin.
Connect in TRUBA.|TRUBA’da bağlanın.
Connect with this job's account in TRUBA.|TRUBA’da bu işin hesabıyla bağlanın.
TRUBA files could not be listed.|TRUBA dosyaları listelenemedi.
Track this job in TRUBA WORKS. Results download automatically when connected.|Bu işi TRUBA’nın WORKS sekmesinden takip edin. Bağlantı kurulduğunda sonuçlar otomatik indirilir.
Open the Connection tab in the TRUBA application|TRUBA uygulamasında Connection sekmesini aç
Connect to TRUBA using the saved VPN profile and SSH account|Kayıtlı VPN profili ve SSH hesabıyla TRUBA’ya bağlan
TRUBA Desktop does not support one-click connection. Restart it with the updated version.|TRUBA Desktop tek tıkla bağlantıyı desteklemiyor. Güncel sürümüyle yeniden başlatın.
Could not reach the running TRUBA application.|Çalışan TRUBA uygulamasına erişilemedi.
The running TRUBA application did not respond.|Çalışan TRUBA uygulaması yanıt vermedi.
Could not start the TRUBA application.|TRUBA uygulaması başlatılamadı.
TRUBA source needs an absolute folder containing main.py and .venv/Scripts/pythonw.exe.|TRUBA kaynağı için main.py ve .venv/Scripts/pythonw.exe dosyalarını içeren klasörün tam yolunu kullanın.
TRUBA.exe was not found. Set TRUBA_DESKTOP_EXE to its full path.|TRUBA.exe bulunamadı. TRUBA_DESKTOP_EXE değişkenine dosyanın tam yolunu girin.
Open Design of Experiments settings.|Deney tasarımı ayarlarını açın.
Select settings method|Ayar yöntemini seçin
Select workspace|Çalışma alanını seçin
Select a method to open its settings|Ayarlarını açmak için bir yöntem seçin
Toggle perspective projection (OFF: orthographic view)|Perspektifi aç/kapat (kapalı: ortografik görünüm)
3D display mode|3B görüntüleme modu
Lowest value included in this variable's range.|Bu değişkenin aralığına dahil en küçük değer.
Highest value included in this variable's range.|Bu değişkenin aralığına dahil en büyük değer.
Edit values directly in the table · Double-click a row to preview · Table data for CSV and Excel|Değerleri tabloda düzenleyin · Önizlemek için satıra çift tıklayın · CSV ve Excel için tablo verileri
Units: mm  N  MPa|Birimler: mm  N  MPa
Units: mm  m/s  Pa|Birimler: mm  m/s  Pa
Units: mm  °C  s  Pa|Birimler: mm  °C  s  Pa
● INITIALIZING|● BAŞLATILIYOR
● ANALYSING|● ANALİZ EDİLİYOR
● BUILDING FE MESH|● SE AĞI OLUŞTURULUYOR
● BUILDING GEOMETRY|● GEOMETRİ OLUŞTURULUYOR
● CANCELLED|● İPTAL EDİLDİ
● COMPLETED|● TAMAMLANDI
● FAILED|● BAŞARISIZ
● GEOMETRY ERROR|● GEOMETRİ HATASI
● MESH CANCELLED|● AĞ İPTAL EDİLDİ
● PENDING APPLY|● UYGULAMA BEKLİYOR
● PREVIEW ERROR|● ÖNİZLEME HATASI
Preview error details|Önizleme hatası ayrıntıları
The mesh worker stopped before returning a preview.|Ağ işlemi, önizlemeyi döndürmeden durdu.
Execution target: {target}|Yürütme hedefi: {target}
Mesh: {element}, global size {size:g} mm|Ağ: {element}, genel boyut {size:g} mm
Last reported stage: {stage}|Bildirilen son aşama: {stage}
Tetrahedral meshing: {method}, local size {size:g} mm|Dört yüzlü ağ: {method}, yerel boyut {size:g} mm
● READY TO SOLVE|● ÇÖZÜME HAZIR
● SOLVER INACTIVE|● ÇÖZÜCÜ ETKİN DEĞİL
● UPDATING PREVIEW|● ÖNİZLEME GÜNCELLENİYOR
● VALIDATION ERROR|● DOĞRULAMA HATASI
'''

TURKISH = dict(line.split('|', 1) for line in _TEXT.splitlines() if '|' in line)

_DETAILS = '''
Absolute Linux path to the OpenCFD OpenFOAM environment (simpleFoam).|OpenCFD OpenFOAM ortamının mutlak Linux yolu (simpleFoam).
Absolute change tolerance in this metric's units, used near zero.|Sıfıra yakın değerler için bu metriğin birimindeki mutlak değişim toleransı.
Automatic refinement stops when two consecutive comparisons meet tolerance.|Art arda iki karşılaştırma toleransı sağladığında otomatik inceltme durur.
Automatic uses recommended load stepping defaults. Advanced applies the values below.|Otomatik, önerilen yük adımı varsayılanlarını kullanır. Gelişmiş, aşağıdaki değerleri uygular.
Budget checked between stages/levels; the active solver finishes its current level.|Süre sınırı aşamalar arasında denetlenir; etkin çözücü mevcut seviyeyi tamamlar.
Build the channel to inspect the water domain; run explicitly to solve velocity and pressure.|Su bölgesini incelemek için kanalı oluşturun; hız ve basıncı çözmek için analizi ayrıca çalıştırın.
Cancel the active analysis and forcibly terminate its process tree.|Etkin analizi iptal edin ve süreç ağacını zorla sonlandırın.
Checking geometry and transfer integrity...|Geometri ve aktarım bütünlüğü denetleniyor...
Coarse to fine, e.g. 0.3, 0.2, 0.15 (mm)|Kabadan inceye, ör. 0.3, 0.2, 0.15 (mm)
Coordinates are mm. Select two opposite parallel planar end faces. Right-click a face to select it.|Koordinatlar mm'dir. Karşılıklı iki paralel düz uç yüzeyi seçin. Seçmek için yüzeye sağ tıklayın.
Deformation ×|Deformasyon ×
Displayed results use this saved case. A new run uses the current Model workspace geometry and the flow inputs at left.|Görüntülenen sonuçlar bu kayıtlı senaryoya aittir. Yeni çalışma, mevcut Model geometrisini ve soldaki akış girdilerini kullanır.
Explicitly install a dedicated per-user Python/Rust computation environment on a SLURM node. Does not change system Python or shell startup files.|Bir SLURM düğümünde kullanıcıya özel Python/Rust hesaplama ortamı kurun. Sistem Python'unu veya kabuk başlangıç dosyalarını değiştirmez.
FE mesh generation progress|SE ağı üretim ilerlemesi
Flow operation stopped. Incomplete case files and logs are retained.|Akış işlemi durduruldu. Eksik senaryo dosyaları ve günlükler korundu.
Fluid/solid coupling exchanges per time step. Increase this or reduce the time step if the reported interface heat imbalance is high.|Zaman adımı başına akışkan/katı bağlaşım sayısı. Arayüz ısı dengesizliği yüksekse artırın veya zaman adımını küçültün.
Free geometry and existing compression setup.|Serbest geometri ve mevcut basma ayarları.
Hot water heats the initially cooler TPMS. Insulated channel exterior; perfect water–solid thermal contact. Flow +Z. Temperatures: °C; time: s.|Sıcak su, başlangıçta daha soğuk TPMS'yi ısıtır. Kanal dışı yalıtılmış, su–katı ısıl teması idealdir. Akış +Z. Sıcaklık: °C; zaman: s.
Immediately terminate the active mesh/solver process tree and cancel remaining levels.|Etkin ağ/çözücü süreç ağacını hemen sonlandırın ve kalan seviyeleri iptal edin.
Initial uniaxial true (Cauchy) yield stress, at zero plastic strain.|Sıfır plastik şekil değiştirmedeki ilk tek eksenli gerçek (Cauchy) akma gerilmesi.
Inputs changed. The displayed case keeps its saved geometry and flow conditions; build/run to apply changes.|Girdiler değişti. Görüntülenen senaryo kayıtlı geometriyi ve akış koşullarını korur; değişiklikler için yeniden oluşturun/çalıştırın.
Jobs keep their own geometry, material, mesh and solver settings. Run starts only checked pending jobs.|İşler kendi geometri, malzeme, ağ ve çözücü ayarlarını korur. Çalıştır yalnız işaretli bekleyen işleri başlatır.
Laminar, single-phase water; constant properties; no buoyancy or external heat loss. Cartesian stair-step surfaces. Check mesh, time-step and coupling refinement. The simulation starts with flowing water and the specified initial temperatures.|Laminer, tek fazlı su; sabit özellikler; kaldırma kuvveti ve dış ısı kaybı yoktur. Yüzeyler Kartezyen basamaklarla temsil edilir. Ağ, zaman adımı ve bağlaşım inceltmesini denetleyin. Benzetim, akan su ve belirtilen ilk sıcaklıklarla başlar.
Maximum difference across the selected metrics, relative to the reference row. Hover over a result for every metric's difference.|Seçili metriklerin referans satırına göre en büyük farkı. Her metriğin farkını görmek için sonucun üzerine gelin.
Maximum physical time step. OpenFOAM may reduce it to satisfy Courant and diffusion limits. Compare a smaller step to check temporal accuracy.|En büyük fiziksel zaman adımı. OpenFOAM, Courant ve yayınım sınırları için küçültebilir. Zamansal doğruluğu daha küçük adımla karşılaştırın.
Mesh generation cancelled. Select Update preview to retry.|Ağ üretimi iptal edildi. Yeniden denemek için Önizlemeyi güncelle'yi seçin.
Not qualified with this contact strategy; use Newton-Raphson.|Bu temas stratejisiyle doğrulanmadı; Newton-Raphson kullanın.
Penalty stiffness multiplier (E / mesh spacing). Increase to reduce penetration; very large values can impede convergence.|Ceza rijitliği çarpanı (E / ağ aralığı). Nüfuziyeti azaltmak için artırın; çok büyük değerler yakınsamayı zorlaştırabilir.
Physical time between saved 3D frames; this does not set the solver's time step.|Kaydedilen 3B kareler arasındaki fiziksel süre; çözücünün zaman adımını belirlemez.
Preparation cancelled. No analysis was queued.|Hazırlık iptal edildi. Kuyruğa analiz eklenmedi.
Remote batch cancellation is not confirmed. Reconnect and retry Stop before closing.|Uzak toplu işin iptali doğrulanmadı. Kapatmadan önce yeniden bağlanıp Durdur'u tekrar deneyin.
Remote cancellation is not confirmed. Reconnect and retry Stop before closing.|Uzak iptal doğrulanmadı. Kapatmadan önce yeniden bağlanıp Durdur'u tekrar deneyin.
Right-click to change the time unit.|Zaman birimini değiştirmek için sağ tıklayın.
Select Gitterburg designs. Configure support/loading faces, then material, mesh and solver in the main workspace. Add each configured model to the analysis queue.|Gitterburg tasarımlarını seçin. Ana çalışma alanında mesnet/yükleme yüzeylerini, ardından malzemeyi, ağı ve çözücüyü ayarlayın. Hazırlanan her modeli analiz kuyruğuna ekleyin.
Select rows to move, remove or preview. Each job retains its saved analysis settings.|Taşımak, kaldırmak veya önizlemek için satırları seçin. Her iş kendi kayıtlı analiz ayarlarını korur.
Self-contact fields show the maximum active slave-facet traction at each original solid node.|Öz temas alanları, özgün katı düğümlerinde etkin bağımlı yüzeylerin en yüksek temas gerilmesini gösterir.
Set the inlet and initial temperatures. Build the water/solid regions, then explicitly run the transient analysis.|Giriş ve başlangıç sıcaklıklarını ayarlayın. Su/katı bölgelerini oluşturup geçici analizi ayrıca çalıştırın.
Slope of true stress versus total logarithmic strain after yield. This is a modulus, not a strain. Must satisfy 0 ≤ Eₜ < E.|Akma sonrasında gerçek gerilmenin toplam logaritmik şekil değiştirmeye göre eğimi. Bu bir modüldür. 0 ≤ Eₜ < E koşulunu sağlamalıdır.
Starting local size; scales with global size at every level.|Başlangıç yerel boyutu; her seviyede genel boyutla ölçeklenir.
Support: green. Compression: orange. Faces validated; ready to import.|Mesnet: yeşil. Basma: turuncu. Yüzeyler doğrulandı; içe aktarmaya hazır.
Table: true (Cauchy) stress and plastic logarithmic strain (dimensionless). First row: zero plastic strain and initial yield stress. CSV also accepts total true strain with the header true_strain,true_stress; elastic strain is subtracted using E. Engineering data is not converted.|Tablo: gerçek (Cauchy) gerilme ve plastik logaritmik şekil değiştirme (boyutsuz). İlk satır: sıfır plastik şekil değiştirme ve ilk akma gerilmesi. CSV, true_strain,true_stress başlığıyla toplam gerçek şekil değiştirmeyi de kabul eder; elastik kısım E ile çıkarılır. Mühendislik verileri dönüştürülmez.
The first CFD mesh approximates cylinder and TPMS surfaces with Cartesian steps. Refine and compare pressure loss before using results. SST uses wall functions; near-wall resolution needs separate validation. Temperature coupling will be added later.|İlk CFD ağı, silindir ve TPMS yüzeylerini Kartezyen basamaklarla yaklaşıklar. Sonuçları kullanmadan önce ağı inceltip basınç kaybını karşılaştırın. SST duvar fonksiyonları kullanır; duvar yakını çözünürlüğü ayrıca doğrulanmalıdır. Sıcaklık bağlaşımı daha sonra eklenecektir.
Thermal properties are independent of the mechanical material. Initial values are a steel-like example; enter the actual material data. Water properties are constant: set them for your experiment.|Isıl özellikler mekanik malzemeden bağımsızdır. Başlangıç değerleri çelik benzeri bir örnektir; gerçek malzeme verilerini girin. Su özellikleri sabittir: deneyinize göre ayarlayın.
This initial transient CHT model supports laminar flow.|Bu ilk geçici CHT modeli laminer akışı destekler.
Use the application's recommended numerical contact parameters. Clear to edit the algorithm, stiffness, penetration tolerances and damping. Contact types and friction coefficients remain your physical inputs.|Uygulamanın önerdiği sayısal temas parametrelerini kullanın. Algoritmayı, rijitliği, nüfuziyet toleranslarını ve sönümü düzenlemek için işareti kaldırın. Temas türleri ve sürtünme katsayıları fiziksel girdileriniz olarak kalır.
Use the selected solver's default CPU count within its license limit.|Seçili çözücünün lisans sınırı içindeki varsayılan CPU sayısını kullanın.
Uses the current Model TPMS, clipped to a concentric cylinder. Flow is along +Z. Specimen length = Model cube size. Geometry: mm; water: SI units.|Mevcut Model TPMS'sini eş merkezli silindire kırpar. Akış +Z yönündedir. Numune uzunluğu = Model küp boyutu. Geometri: mm; su: SI birimleri.
Waiting for TRUBA job and retrieving results…|TRUBA işi bekleniyor ve sonuçlar alınıyor…
e.g. Ti-6Al-4V ELI / 316L SS|ör. Ti-6Al-4V ELI / 316L SS
Internal self-contact|İç öz temas
Self-friction μ_self|Öz sürtünme μ_self
Frictional self-contact (Coulomb μ_self)|Sürtünmeli öz temas (Coulomb μ_self)
Frictionless self-contact (μ_self = 0)|Sürtünmesiz öz temas (μ_self = 0)
Numerical settings|Sayısal ayarlar
Load stepping|Yük adımları
Loading control|Yükleme kontrolü
Normal stiffness (FKN)|Normal rijitlik (FKN)
Penetration tol. (FTOLN)|Nüfuziyet tol. (FTOLN)
Platen penetration tol.|Plaka nüfuziyet tol.
Period / pore scale|Periyot / Gözenek ölçeği
Local mesh size|Yerel ağ boyutu
Starting size (mm)|Başlangıç boyutu (mm)
Linear solver|Doğrusal çözücü
Max iterations|En fazla yineleme
Newton iterations|Newton yinelemeleri
Newton strategy|Newton stratejisi
Kratos mechanics|Kratos mekaniği
Kratos · loading|Kratos · Yükleme
Kratos · nonlinear solution|Kratos · Doğrusal olmayan çözüm
Kratos results|Kratos sonuçları
Max Von Mises (MPa)|En yüksek von Mises (MPa)
Poisson Ratio (ν*)|Poisson oranı (ν*)
Poisson ratio|Poisson oranı
Modulus|Modül
Modulus (MPa)|Modül (MPa)
Modulus difference (%)|Modül farkı (%)
Peak force (N)|En yüksek kuvvet (N)
Measured deformation (mm)|Ölçülen deformasyon (mm)
Max Δ / ref.|En büyük Δ / ref.
Level|Seviye
Intermediate|Ara seviye
Minimum|En az
Maximum|En fazla
Manual|Elle
Local (mm)|Yerel (mm)
Mesh (s)|Ağ (s)
Solve (s)|Çözüm (s)
Mesh / elements|Ağ / Elemanlar
Min: not specified|Min: belirtilmemiş
not specified|belirtilmemiş
not confirmed|doğrulanmadı
Live Slurm limits|Güncel Slurm sınırları
Memory|Bellek
Time budget reached; convergence not confirmed.|Süre sınırına ulaşıldı; yakınsama doğrulanmadı.
Attempt limit reached; convergence not confirmed.|Deneme sınırına ulaşıldı; yakınsama doğrulanmadı.
Minimum mesh size reached; convergence not confirmed.|En küçük ağ boyutuna ulaşıldı; yakınsama doğrulanmadı.
Stopped; convergence not confirmed. Partial results can be exported.|Durduruldu; yakınsama doğrulanmadı. Kısmi sonuçlar dışa aktarılabilir.
Manual levels exhausted; convergence not confirmed.|Elle girilen seviyeler tamamlandı; yakınsama doğrulanmadı.
No reference comparison yet.|Henüz referans karşılaştırması yok.
Move up|Yukarı taşı
Move down|Aşağı taşı
Send to RustMech|RustMech'e gönder
Open case folder|Senaryo klasörünü aç
Open flow case|Akış senaryosunu aç
Open saved flow case|Kayıtlı akış senaryosunu aç
Open thermal case|Isıl senaryoyu aç
Max time step / s|En büyük zaman adımı / s
Inlet extension / mm|Giriş uzantısı / mm
Outlet extension / mm|Çıkış uzantısı / mm
Inlet speed / m/s|Giriş hızı / m/s
Initial water / °C|Başlangıç su / °C
Initial solid / °C|Başlangıç katı / °C
Inlet water / °C|Giriş suyu / °C
Inlet water|Giriş suyu
Outlet water|Çıkış suyu
OpenFOAM bashrc|OpenFOAM bashrc
Cannot start analysis|Analiz başlatılamadı
Cannot save settings|Ayarlar kaydedilemedi
Cannot save material|Malzeme kaydedilemedi
Cannot load material|Malzeme yüklenemedi
Cannot import curve|Eğri içe aktarılamadı
Invalid convergence plan|Geçersiz yakınsama planı
Invalid experiment range|Geçersiz deney aralığı
Import Error|İçe aktarma hatası
Export Error|Dışa aktarma hatası
Force-displacement CSV|Kuvvet–yer değiştirme CSV
Export Design of Experiments Table (CSV / Excel)|Deney tasarımı tablosunu dışa aktar (CSV / Excel)
Sheet (Shell)|Levha (kabuk)
Network A (Solid)|Ağımsı A (katı)
Network B (Inverted Solid)|Ağımsı B (ters katı)
Truss Lattice|Çubuk kafes
Porous|Gözenekli
Tension|Çekme
Flexure|Eğilme
Both|İkisi
'''
TURKISH.update(line.split('|', 1) for line in _DETAILS.splitlines() if '|' in line)
TURKISH.update({
    'Custom experiment': 'Özel deney',
    'Solid': 'Katı',
    'ISO 13314:2011 · Cellular metal compression': 'ISO 13314:2011 · Hücresel metal basma',
    'ASTM E8/E8M-25 · Flat subsize tension': 'ASTM E8/E8M-25 · Düz küçük boy çekme',
    'ISO 178:2019 · Three-point flexure': 'ISO 178:2019 · Üç noktalı eğilme',
    'RustMech | Nonlinear Compression': 'RustMech | Doğrusal olmayan basma',
    'RustMech · Kratos results': 'RustMech · Kratos sonuçları',
    'HEX20/TET10 compression uses displacement control and penalty mortar.\nSelf-contact: HEX8, penalty mortar, fixed increments. Increase penalty to reduce penetration; refine increments if convergence fails.':
        'HEX20/TET10 basma analizinde yer değiştirme kontrolü ve ceza mortar kullanılır.\nÖz temas: HEX8, ceza mortar, sabit artımlar. Nüfuziyeti azaltmak için ceza katsayısını artırın; yakınsama yoksa artımları küçültün.',
    'Kratos HEX20/TET10 compression uses displacement control and penalty mortar with self-contact disabled.':
        'Kratos HEX20/TET10 basma analizinde yer değiştirme kontrolü ve ceza mortar kullanılır. Öz temas kapalı olmalıdır.',
    'Kratos three-point flexure requires HEX8 with aligned support/loading lines.':
        'Kratos üç nokta eğme analizi, mesnet ve yükleme çizgileriyle hizalı HEX8 ağ gerektirir.',
    'Kratos HEX20 and TET10 platen contact requires penalty mortar.':
        'Kratos HEX20 ve TET10 plaka teması için ceza mortar gerekir.',
    'Kratos HEX20 and TET10 compression requires displacement control.':
        'Kratos HEX20 ve TET10 basma analizi yer değiştirme kontrolü gerektirir.',
    'Kratos self-contact requires HEX8.': 'Kratos öz temas için HEX8 gerektirir.',
    '• Bottom platen at Z = 0 · Top platen at Z = L (−Z compression)\n• Rigid platen contact with separate Coulomb friction coefficients\n• Configurable 3D Cell-Wall Self-Contact & Internal Friction (μ_self)\n• Large displacement (NLGEOM) · voxel HEX8/HEX20 or surface-conforming TET4/TET10':
        '• Alt plaka Z = 0 · Üst plaka Z = L (−Z basma)\n• Ayrı Coulomb sürtünme katsayılarıyla rijit plaka teması\n• Ayarlanabilir 3B hücre duvarı öz teması ve iç sürtünme (μ_self)\n• Büyük yer değiştirme (NLGEOM) · Voksel HEX8/HEX20 veya yüzeye uyumlu TET4/TET10',
})
_MORE = '''
Global mesh size|Genel ağ boyutu
Mesh decimal places|Ağ ondalık hane sayısı
Compression force|Basma kuvveti
Voxel spacing|Voksel aralığı
Solid fraction|Katı oranı
Meshing method|Ağ oluşturma yöntemi
Material Point Method|Malzeme noktası yöntemi
Peridynamics|Peridinamik
FFT Homogenization|FFT homojenleştirme
Voxels per axis|Eksen başına voksel
Run locally|Bu bilgisayarda çalıştır
Run on TRUBA|TRUBA’da çalıştır
Run on TRUBA as one job|TRUBA’ya tek iş olarak gönder
Run on TRUBA as separate jobs|TRUBA’ya ayrı işler olarak gönder
Run settings|Çalıştırma ayarları
Run location|Çalıştırma yeri
Experiments per script|Betik başına deney
Concurrent experiments|Eşzamanlı deney sayısı
Experiments share the CPU cores and RAM of one Slurm job.|Deneyler aynı Slurm işinin CPU çekirdeklerini ve belleğini paylaşır.
TRUBA (one job)|TRUBA (tek iş)
A TRUBA batch must fit within the experiment matrix.|TRUBA işindeki deneyler matrisin sınırları içinde olmalıdır.
Paired TRUBA experiments require two rows, CPUs and MiB of RAM.|İkili TRUBA gönderimi için iki deney, en az iki çekirdek ve 2 MiB RAM gerekir.
Running two experiments in parallel|İki deney paralel çalışıyor
Awaiting results|Sonuçlar bekleniyor
Connect to TRUBA, then run the matrix again.|TRUBA bağlantısını kurduktan sonra matrisi yeniden çalıştırın.
Running experiment matrix on TRUBA.|Deney matrisi TRUBA’da çalıştırılıyor.
Experiment matrix stopped.|Deney matrisi durduruldu.
Remote experiment detached.|TRUBA işi arka planda izlemeye alındı.
Excel report could not be saved|Excel raporu kaydedilemedi
Create matrix|Matris oluştur
Fixed / minimum|Sabit / alt sınır
Min / max|Min / maks
Value list|Değer listesi
Input mode|Giriş biçimi
Outer X (mm)|Dış ölçü X (mm)
Outer Y (mm)|Dış ölçü Y (mm)
Outer Z (mm)|Dış ölçü Z (mm)
For periodic FFT, each outer dimension must be a whole multiple of the cell period.|Periyodik FFT için her dış ölçü, hücre periyodunun tam katı olmalıdır.
Solid material|Katı malzeme
Fluid material|Akışkan malzeme
Generate the selected experiment geometry, or the first geometry in the draft.|Seçili deneyin geometrisini veya taslaktaki ilk geometriyi oluşturur.
Separate values with commas. For decimal commas, use a semicolon between values.|Değerleri virgülle ayırın. Ondalık virgül kullanırken değerleri noktalı virgülle ayırın.
Maximum / values|Üst sınır / değerler
Select values|Değerleri seç
Variable|Değişken
Results folder|Sonuç klasörü
Structure family / type|Yapı ailesi / türü
Solid fraction (%)|Katı oranı (%)
Stopped. Finished results are saved.|Durduruldu. Tamamlanan deneylerin sonuçları kaydedildi.
Void / solid stiffness|Boşluk / katı rijitliği
Ratio of the Young’s modulus assigned to void voxels to that of the solid (Eᵥ/Eₛ). For example, 0.01 gives the voids 1% of the solid stiffness. Lower values better approximate empty space but can make the FFT solver harder to converge. Higher values can overestimate the cell’s effective stiffness.|Boşluk voksellerine atanan Young modülünün katının Young modülüne oranıdır (Eᵥ/Eₛ). Örneğin 0,01 değerinde boşluklara katı rijitliğinin %1’i atanır. Düşük değerler gerçek boşluğu daha iyi temsil eder ancak FFT çözücüsünün yakınsamasını zorlaştırabilir. Yüksek değerler hücrenin efektif rijitliğini olduğundan büyük gösterebilir.
DAMASK voxel preview unavailable. Check the settings below.|DAMASK voksel önizlemesi kullanılamıyor. Aşağıdaki ayarları kontrol edin.
Preparing DAMASK voxel preview…|DAMASK voksel önizlemesi hazırlanıyor…
The void phase has the specified fraction of the solid stiffness.|Boşluk fazının rijitliği, katının girilen oranı kadardır.
Solid phase material|Katı faz malzemesi
Effective stiffness matrix (MPa)|Etkin rijitlik matrisi (MPa)
Last completed FFT result|Son tamamlanan FFT sonucu
Open FFT results|FFT sonuçlarını aç
FFT requires a procedural periodic TPMS or lattice cell.|FFT, periyodik TPMS veya kafes birim hücresi gerektirir.
FFT requires a positive Young's modulus.|FFT için Young modülü pozitif olmalıdır.
FFT requires a Poisson ratio between -1 and 0.5.|FFT için Poisson oranı -1 ile 0,5 arasında olmalıdır.
Ready to run local DAMASK FFT.|Yerel DAMASK FFT çalıştırmaya hazır.
Ready to submit DAMASK FFT to TRUBA.|DAMASK FFT, TRUBA'ya gönderilmeye hazır.
Enter a WSL distribution for DAMASK.|DAMASK için WSL dağıtım adını girin.
Sampling the periodic cell…|Periyodik hücre örnekleniyor…
Crack initiation and propagation in mechanical tests.|Mekanik deneylerde çatlak başlangıcı ve ilerlemesi.
Effective stiffness from periodic unit-cell tests.|Periyodik birim hücre deneylerinden etkin rijitlik.
Bilinear approximation: E, true yield stress and Eₜ. Choose the true stress–strain curve when measured hardening data is available.|Çift doğrulu yaklaşım: E, gerçek akma gerilmesi ve Eₜ. Ölçülmüş pekleşme verisi varsa gerçek gerilme–şekil değiştirme eğrisini seçin.
Uses each geometry's selected variables. Without variables, only its reference case is generated.|Her geometrinin seçili değişkenlerini kullanır. Değişken yoksa yalnız referans deneyi oluşturulur.
1 reference case|1 referans deneyi
Top platen displacement in −Z; reaction force is calculated (N).|Üst plakanın −Z yönündeki yer değiştirmesi; tepki kuvveti hesaplanır (N).
Pending / active remote jobs|Bekleyen / Etkin uzak işler
TRUBA jobs|TRUBA işleri
DoE · TRUBA jobs|DoE · TRUBA işleri
Active jobs|Aktif işler
Queue status|Kuyruk durumu
Allocated CPU|Ayrılmış CPU
Idle CPU|Boş CPU
Other CPU|Diğer CPU
Idle nodes|Boş düğüm
Idle nodes / total nodes|Boş düğüm / toplam düğüm
CPUs in other Slurm states, such as drained or down.|Devre dışı veya boşaltılmış gibi diğer Slurm durumlarındaki CPU'lar.
Accepting jobs|İş alıyor
Down|Kapalı
Draining|Boşaltılıyor
Inactive|Etkin değil
Connect to TRUBA to see queue status.|Kuyruk durumunu görmek için TRUBA'ya bağlanın.
Updating queue status…|Kuyruk durumu güncelleniyor…
Could not update queue status.|Kuyruk durumu güncellenemedi.
No visible Slurm queues.|Görüntülenebilen Slurm kuyruğu yok.
Last updated|Son güncelleme
Estimated start|Tahmini başlangıç
Pending reason|Bekleme nedeni
Waiting for resources|Kaynak bekleniyor
Higher-priority jobs|Daha öncelikli işler var
Waiting for a job dependency|Bağımlı iş bekleniyor
Awaiting scheduler review|Slurm değerlendirmesi bekleniyor
Account CPU limit reached|Hesabın CPU sınırına ulaşıldı
QoS CPU limit reached|QoS CPU sınırına ulaşıldı
Slurm has no start estimate yet.|Slurm henüz başlangıç tahmini vermedi.
Start time estimated by Slurm. It may change.|Slurm'un tahmini başlangıç zamanı değişebilir.
Cancel job|İşi iptal et
The selected jobs changed. Review the list and try again.|Seçili işler değişti. Listeyi kontrol edip tekrar deneyin.
Only individual Slurm allocations can be cancelled here.|Buradan yalnızca tekil Slurm tahsisleri iptal edilebilir.
Sending cancellation request to Slurm…|Slurm’a iptal isteği gönderiliyor…
All jobs|Tüm işler
Work name for new jobs|Yeni işler için çalışma adı
Use the same work name to group multiple submissions.|Birden çok gönderimi gruplamak için aynı çalışma adını kullanın.
Work name|Çalışma adı
Work|Çalışma
Job|İş
Command|Komut
Slurm job ID|Slurm iş numarası
Slurm ID|Slurm no.
Job description|İş açıklaması
Started at|Başlangıç zamanı
Elapsed time|Geçen süre
Time left in Slurm allocation|Slurm süre sınırına kalan
Time left|Kalan süre
Script / command|Betik / komut
CPUs|CPU sayısı
Partition|Slurm bölümü
Connect to TRUBA to see active jobs.|Aktif işleri görmek için TRUBA'ya bağlanın.
No active Slurm jobs.|Aktif Slurm işi yok.
Updating active jobs…|Aktif işler güncelleniyor…
Could not update active jobs. Retrying…|Aktif işler güncellenemedi. Yeniden deneniyor…
RUNNING|Çalışıyor
PENDING|Kuyrukta
CONFIGURING|Hazırlanıyor
COMPLETING|Tamamlanıyor
SUSPENDED|Askıda
Analysis code / purpose|Analiz kodu / Açıklama
No saved TRUBA jobs.|Kayıtlı TRUBA işi yok.
Background analyses update and download automatically when connected.|TRUBA'ya bağlanınca analiz durumları güncellenir ve sonuçlar otomatik indirilir.
Preparing analysis for TRUBA…|Analiz TRUBA için hazırlanıyor…
Preparing analysis…|Analiz hazırlanıyor…
Uploading analysis inputs…|Analiz girdileri yükleniyor…
Submitting analysis to TRUBA…|Analiz TRUBA'ya gönderiliyor…
Analysis submitted|Analiz gönderildi
waiting for Slurm|Slurm bekleniyor
Analysis queued|Analiz kuyrukta
Slurm is preparing resources…|Slurm kaynakları hazırlıyor…
Analysis started and running|Analiz başladı ve çalışıyor
Analysis is finishing…|Analiz tamamlanıyor…
Downloading results…|Sonuçlar indiriliyor…
Analysis complete|Analiz tamamlandı
Analysis complete · results downloaded|Analiz tamamlandı · sonuçlar indirildi
results downloaded|sonuçlar indirildi
See TRUBA jobs for this analysis. Results download automatically when connected.|Bu analiz için TRUBA işleri listesine bakın. Bağlandığınızda sonuçlar otomatik indirilir.
Waiting for TRUBA submission confirmation. Close once the Slurm job number is visible.|TRUBA gönderim onayı bekleniyor. Slurm iş numarası göründüğünde programı kapatabilirsiniz.
Saved job states · Connect to cancel a job.|Kayıtlı iş durumları · İptal etmek için bağlanın.
Saved job states · Connect with the owning account to cancel.|Kayıtlı iş durumları · İptal etmek için işi gönderen hesapla bağlanın.
No pending remote jobs in saved records.|Kayıtlarda bekleyen uzak iş yok.
Job / purpose|İş / Amaç
Saved state|Kayıtlı durum
Runtime capability check|Çalışma ortamı yetenek denetimi
Remote runtime setup|Uzak ortam kurulumu
Mesh preview|Ağ önizlemesi
Surface generation|Yüzey üretimi
Mechanical analysis|Mekanik analiz
Mechanical analysis (Kratos)|Mekanik analiz (Kratos)
Resource session|Kaynak oturumu
Mesh convergence|Ağ yakınsaması
Slurm job ID|Slurm iş kimliği
Slurm script|Slurm betiği
Slurm script plan|Slurm betik planı
Slurm queue and resource limits|Slurm kuyruğu ve kaynak sınırları
New script|Yeni betik
Script RAM|Betik RAM’i
RAM per mesh (GiB)|Ağ başına RAM (GiB)
Mesh (mm)|Ağ (mm)
Remove selected mesh levels|Seçili ağ seviyelerini sil
Click any cell to select its row, then click Delete.|Satırı seçmek için herhangi bir hücresine, ardından Sil'e tıklayın.
Slurm plan ready|Slurm planı hazır
Planned levels finished; convergence not confirmed.|Planlanan seviyeler bitti. Yakınsama doğrulanmadı.
Slurm state|Slurm durumu
Wall time per analysis|Analiz başına süre sınırı
Slurm time limit for each mesh analysis (hours:minutes).|Her ağ analizi için Slurm süre sınırı (saat:dakika).
Slurm time limit for each mesh analysis, in minutes.|Her ağ analizi için dakika cinsinden Slurm süre sınırı.
Automatic CPU and RAM sharing|Otomatik CPU ve RAM paylaşımı
Two levels share one Slurm allocation when each can use at least 16 CPUs. A single level uses the full allocation.|Her birine en az 16 CPU düşüyorsa iki seviye tek Slurm tahsisini paylaşır. Tek seviye tahsisin tamamını kullanır.
Solver CPUs|Çözücü CPU
Allocated RAM (GiB)|Ayrılan RAM (GiB)
Peak RAM (GiB)|Tepe RAM (GiB)
CPU threads assigned to this solver step.|Bu çözüm adımına ayrılan CPU iş parçacıkları.
RAM reserved for this mesh level.|Bu ağ seviyesine ayrılan RAM.
Peak RAM reported by Slurm for this level after the job ends.|İş bittikten sonra Slurm'un bu seviye için bildirdiği tepe RAM.
DOE mesh generation|DoE ağı üretimi
DOE analysis|DoE analizi
Flow export|Akış dışa aktarımı
Flow analysis|Akış analizi
Thermal export|Isıl dışa aktarım
Thermal analysis|Isıl analiz
Cancelling…|İptal ediliyor…
Cancellation failed. Check the connection and retry.|İptal başarısız. Bağlantıyı kontrol edip yeniden deneyin.
Remote job cancelled.|Uzak iş iptal edildi.
preparing|hazırlanıyor
unknown|bilinmiyor
interrupted; resume using this job record|kesintiye uğradı; bu iş kaydıyla devam edin
No resource allocation.|Kaynak ayrılmadı.
Copy text|Metni kopyala
Copy selection|Seçimi kopyala
Copy entire table with headers|Tüm tabloyu başlıklarıyla kopyala
Select all|Tümünü seç
RAM maximum follows CPU count; account/QOS and job-submission rules are checked by Slurm.|RAM üst sınırı CPU sayısına bağlıdır; hesap/QOS ve iş gönderim kurallarını Slurm denetler.
Existing allocation restored unchanged.|Mevcut kaynak tahsisi değiştirilmeden geri yüklendi.
Orkinos may require a TRUBA reservation.|Orkinos için TRUBA rezervasyonu gerekebilir.
GPU queue: limits can be inspected and edited; allocation is unavailable because current solvers use CPUs.|GPU kuyruğu: sınırlar incelenebilir ve düzenlenebilir; mevcut çözücüler CPU kullandığı için kaynak ayrılamaz.
'''
TURKISH.update(line.split('|', 1) for line in _MORE.splitlines() if '|' in line)

_GEOMETRY_ENGLISH = {
    'tetra_strategy': 'Auto meshes and repeats a unit cell for eligible periodic TPMS and truss lattices, checking matching faces, element quality and reference-volume error. It falls back to Netgen for unsupported geometry or an exceeded element budget. Periodic cells explains why a structure is ineligible. The cell method applies local size to the entire cell; very small sizes are expensive and may produce more elements with lower shape quality than Netgen. For a 6 mm / 3 mm Gyroid, try a local size of 0.10 mm.',
    'structure_family': 'Select the structure family: TPMS, Truss Lattice or Porous. The structure-type list changes with this selection.',
    'structure_name': 'Choose the geometric pattern within the selected family, such as Gyroid. Different patterns produce different wall and connectivity arrangements at the same size and solid fraction.',
    'tpms_morphology': 'Choose how solid material is distributed. Sheet creates walls around the surface; Network A and B use the two complementary sides as solid.',
    'size': 'Length of one edge of the cubic specimen (mm). This sets the overall model size.',
    'cell_size': 'Unit-cell period for periodic structures, or characteristic pore scale for porous structures (mm). Smaller values fit more cells or pores into the specimen. Must not exceed cube size.',
    'solid_fraction': 'Target percentage of specimen volume occupied by solid material. For example, 30% solid corresponds to approximately 70% void. Discretization can cause the generated mesh to have a different actual solid fraction.',
    'mesh_resolution': 'Global finite-element size (mm). For TET4/TET10 it is the global upper size bound; for HEX8/HEX20 it is the target voxel spacing, adjusted to fit the cube. Smaller values increase element count, memory and generation/solve time. Reference geometry is prepared automatically and does not change with mesh size. A coarse mesh may approximate geometry with more angular surfaces.',
    'local_mesh_size': 'Local target element size for TET4/TET10 only (mm). Refines the top/bottom platen-contact surfaces and detected thin walls up to approximately five times this thickness. Helps represent contact and thin-wall details. Must not exceed global FE mesh size; for example, global 0.20 mm and local 0.05 mm. Smaller values can increase element count, memory and generation/solve time and do not alone guarantee solution accuracy. The new-project default is 0.05 mm. The periodic-cell method applies local size to the whole cell.',
    'mesh_type': 'Choose the finite-element type. HEX8/HEX20 use voxel-based 8/20-node hexahedra; TET4/TET10 use surface-conforming 4/10-node tetrahedra. HEX20 and TET10 are quadratic and contain more nodes. Local mesh size applies only to tetrahedral types.',
    'random_seed': 'Seed for random porous-structure generation. With other settings unchanged, the same seed produces the same geometry; a different seed produces a different pore arrangement.',
}
TURKISH.update({english: GEOMETRY_HELP[name] for name, english in _GEOMETRY_ENGLISH.items()})
TURKISH.update({
    'Active · HEX8 / HEX20 / TET4 / TET10 · PyMAPDL SMP': 'Etkin · HEX8 / HEX20 / TET4 / TET10 · PyMAPDL SMP',
    'Elastic response only; plastic deformation is not modeled.': 'Yalnız elastik davranış; plastik deformasyon modellenmez.',
    'Multilinear isotropic hardening from a true stress–strain curve. E defines the elastic part; the table defines plastic hardening.': 'Gerçek gerilme–şekil değiştirme eğrisinden çok doğrulu izotropik pekleşme. E elastik kısmı, tablo plastik pekleşmeyi tanımlar.',
    'Operation progress': 'İşlem ilerlemesi',
    'Checking TRUBA resource limits…': 'TRUBA kaynak sınırları denetleniyor…',
})
_VALIDATION = '''
Unsupported loading mode.|Desteklenmeyen yükleme türü.
Applied compression force must be positive and finite (N).|Uygulanan basma kuvveti pozitif ve sonlu olmalıdır (N).
Unsupported solver backend.|Desteklenmeyen çözücü.
Unsupported self-contact mode.|Desteklenmeyen öz temas türü.
Internal self-friction coefficient μ_self must be between 0 and 2.|İç öz sürtünme katsayısı μ_self, 0 ile 2 arasında olmalıdır.
Unsupported contact formulation.|Desteklenmeyen temas formülasyonu.
Normal contact stiffness factor (FKN) must be between 0.01 and 10.|Normal temas rijitliği katsayısı (FKN), 0.01 ile 10 arasında olmalıdır.
Penetration tolerance factor (FTOLN) must be between 0.001 and 0.5.|Nüfuziyet toleransı katsayısı (FTOLN), 0.001 ile 0.5 arasında olmalıdır.
Contact damping factor must be between 0 and 1.|Temas sönüm katsayısı 0 ile 1 arasında olmalıdır.
Platen penetration tolerance factor must be between 0.000001 and 0.5.|Plaka nüfuziyet toleransı katsayısı 0.000001 ile 0.5 arasında olmalıdır.
Compression must be a positive finite number.|Basma miktarı pozitif ve sonlu olmalıdır.
Compression must be smaller than cube size.|Basma miktarı küp boyutundan küçük olmalıdır.
Substeps must satisfy minimum ≤ initial ≤ maximum.|Alt adımlar, en az ≤ başlangıç ≤ en fazla koşulunu sağlamalıdır.
Enter a material name.|Malzeme adı girin.
Unsupported material model.|Desteklenmeyen malzeme modeli.
Material density must be positive and finite.|Malzeme yoğunluğu pozitif ve sonlu olmalıdır.
Young's modulus must be positive and finite.|Young modülü pozitif ve sonlu olmalıdır.
Poisson ratio must be between -1 and 0.5.|Poisson oranı -1 ile 0.5 arasında olmalıdır.
Yield stress must be positive and finite.|Akma gerilmesi pozitif ve sonlu olmalıdır.
Tangent modulus must be >= 0 and < Young's modulus.|Teğet modülü sıfır veya pozitif ve Young modülünden küçük olmalıdır.
User curve needs at least two plastic-strain / true-stress points.|Kullanıcı eğrisi en az iki plastik şekil değiştirme / gerçek gerilme noktası içermelidir.
Every curve row needs two finite numbers: plastic strain, true stress.|Her eğri satırında iki sonlu sayı olmalıdır: plastik şekil değiştirme ve gerçek gerilme.
Curve must start at zero plastic strain, with increasing strain and positive stress.|Eğri sıfır plastik şekil değiştirmede başlamalı; şekil değiştirme artmalı ve gerilme pozitif olmalıdır.
This isotropic hardening model requires nondecreasing stress; softening is unsupported.|Bu izotropik pekleşme modeli azalmayan gerilme gerektirir; yumuşama desteklenmez.
True curve needs at least two finite strain / stress pairs.|Gerçek eğri en az iki sonlu şekil değiştirme / gerilme çifti içermelidir.
Total true strain must be nonnegative and strictly increasing.|Toplam gerçek şekil değiştirme sıfır veya pozitif ve kesin artan olmalıdır.
Period / characteristic size must not exceed cube size.|Periyot / karakteristik boyut küp boyutunu aşamaz.
Solid fraction must be between 0 and 100%.|Katı oranı %0 ile %100 arasında olmalıdır.
Local mesh size must not exceed the global FE mesh size.|Yerel ağ boyutu genel SE ağı boyutunu aşamaz.
Local mesh size must not exceed the global mesh size.|Yerel ağ boyutu genel ağ boyutunu aşamaz.
Unsupported structure family.|Desteklenmeyen yapı ailesi.
Unsupported structure morphology.|Desteklenmeyen yapı morfolojisi.
Unsupported FE mesh type.|Desteklenmeyen SE ağı türü.
Unsupported tetrahedral mesh strategy.|Desteklenmeyen tetra ağ stratejisi.
FE solid must intersect both top and bottom faces.|SE katısı hem alt hem üst yüzeyle kesişmelidir.
Hamsi requires 56 CPU cores per node. Select 56 cores or choose another queue.|Hamsi düğüm başına 56 işlemci çekirdeği gerektirir. 56 çekirdek veya başka bir kuyruk seçin.
size|Boyut
cell_size|Hücre boyutu
solid_fraction|Katı oranı
mesh_resolution|Genel ağ boyutu
local_mesh_size|Yerel ağ boyutu
cores|CPU çekirdeği
substeps|Alt adımlar
minimum_substeps|En az alt adım
maximum_substeps|En fazla alt adım
'''
TURKISH.update(line.split('|', 1) for line in _VALIDATION.splitlines() if '|' in line)
FIELD_LABELS = {key: TURKISH.pop(key) for key in (
    'size', 'cell_size', 'solid_fraction', 'mesh_resolution', 'local_mesh_size',
    'cores', 'substeps', 'minimum_substeps', 'maximum_substeps')}

# Full-message templates preserve numerical values, paths and solver names.
TURKISH.update({
    'Library': 'Kütüphane',
    'Material library': 'Malzeme kütüphanesi',
    'Select material': 'Malzeme seç',
    'Material selection': 'Malzeme seçimi',
    'Current / custom material': 'Mevcut / özel malzeme',
    'Browse material properties and source datasheets': 'Malzeme özelliklerini ve kaynak teknik föylerini incele',
    'Search materials...': 'Malzeme ara...',
    'Search materials': 'Malzeme ara',
    'Materials': 'Malzemeler',
    'Material properties and sources': 'Malzeme özellikleri ve kaynakları',
    'No matching materials.': 'Eşleşen malzeme yok.',
    'Reference temperature': 'Referans sıcaklığı',
    'Constant properties. Solids use linear elasticity; no plastic curve is supplied.': 'Sabit özellikler. Katılarda doğrusal elastik model kullanılır; plastik eğri sağlanmaz.',
    'Properties': 'Özellikler',
    'Not supplied': 'Sağlanmamış',
    'Engineering assumptions': 'Mühendislik varsayımları',
    'Conditions and limitations': 'Koşullar ve sınırlamalar',
    'Datasheets and references': 'Teknik föyler ve kaynaklar',
    'Required properties not supplied:': 'Gerekli özellikler sağlanmamış:',
    'Add material': 'Malzeme ekle',
    'New material': 'Yeni malzeme',
    'Use': 'Kullan',
    'Temperature-dependent': 'Sıcaklığa bağlı',
    'Material saved to the library.': 'Malzeme kütüphaneye kaydedildi.',
    'A material with this name already exists.': 'Bu adda bir malzeme zaten var.',
    'Could not save the material library.': 'Malzeme kütüphanesi kaydedilemedi.',
    'Invalid density value.': 'Yoğunluk sıfırdan büyük olmalı.',
    'Invalid young_modulus value.': 'Elastisite modülü sıfırdan büyük olmalı.',
    'Invalid poisson_ratio value.': 'Poisson oranı -1 ile 0,5 arasında olmalı.',
    'Invalid conductivity value.': 'Isıl iletkenlik sıfırdan büyük olmalı.',
    'Invalid heat_capacity value.': 'Özgül ısı kapasitesi sıfırdan büyük olmalı.',
    'Invalid dynamic_viscosity value.': 'Dinamik viskozite sıfırdan büyük olmalı.',
    'Includes engineering estimates; see marked values and assumptions below.': 'Mühendislik tahminleri içerir; işaretli değerleri ve aşağıdaki varsayımları inceleyin.',
    'estimate': 'tahmin',
    'Density': 'Yoğunluk',
    "Young's modulus": 'Elastisite modülü',
    'Yield stress': 'Akma gerilmesi',
    'Tangent modulus': 'Teğet modülü',
    'Thermal conductivity': 'Isıl iletkenlik',
    'Specific heat capacity': 'Özgül ısı kapasitesi',
    'Thermal expansion': 'Isıl genleşme',
    'Dynamic viscosity': 'Dinamik viskozite',
})

TEMPLATES = {
    '{count} experiments ready.': '{count} deney hazır.',
    'Select a structure type for {family}.': '{family} için bir yapı türü seçin.',
    'Running experiment {index} / {total}': 'Deney çalıştırılıyor {index} / {total}',
    '{count} experiments finished · {failed} failed.': '{count} deney tamamlandı · {failed} hata.',
    'Permanently delete {count} selected files from TRUBA?':
        'Seçilen {count} dosya TRUBA’dan kalıcı olarak silinsin mi?',
    'Deleted {count} files from TRUBA.': 'TRUBA’dan {count} dosya silindi.',
    'Deleted {count} files from TRUBA. Failed: {failed}.':
        'TRUBA’dan {count} dosya silindi. Silinemeyen: {failed}.',
    'Cancel Slurm job {job_id}? This stops the entire allocation and any analyses using it.':
        '{job_id} numaralı Slurm işi iptal edilsin mi? Tahsisin tamamı ve onu kullanan analizler durdurulur.',
    'Cancel {count} selected Slurm jobs? This stops each allocation and any analyses using it.':
        'Seçilen {count} Slurm işi iptal edilsin mi? Bu işlerin tahsisleri ve onları kullanan analizler durdurulur.',
    'Could not request cancellation for Slurm job {job_id}. Refresh and retry.':
        '{job_id} numaralı Slurm işi için iptal isteği gönderilemedi. Listeyi yenileyip tekrar deneyin.',
    'Cancellation requested for Slurm job {job_id}.':
        '{job_id} numaralı Slurm işi için iptal isteği gönderildi.',
    'Could not confirm cancellation requests for all {count} Slurm jobs. Check their status.':
        '{count} Slurm işinin tamamı için iptal isteği doğrulanamadı. İşlerin durumunu kontrol edin.',
    'Cancellation requested for {count} Slurm jobs.':
        '{count} Slurm işi için iptal isteği gönderildi.',
    'Could not write VTU: {file}': 'VTU dosyası yazılamadı: {file}',
    'Missing or invalid nodal result field: {field}': 'Düğüm sonuç alanı eksik veya geçersiz: {field}',
    'Unsupported EnSight Gold cell types: {types}': 'Desteklenmeyen EnSight Gold eleman tipleri: {types}',
    'Preview error: {error}': 'Önizleme hatası: {error}',
    'Local Kratos {version} is ready.': 'Yerel Kratos {version} hazır.',
    'Could not build the DAMASK voxel preview: {detail}': 'DAMASK voksel önizlemesi oluşturulamadı: {detail}',
    '{grid} · Voxel spacing: {spacing} mm\nSolid voxels: {count} ({fraction}) · Void voxels: {void}\nVoid stiffness: {ratio} of solid · Void E: {modulus} MPa':
        '{grid} · Voksel aralığı: {spacing} mm\nKatı vokseller: {count} ({fraction}) · Boşluk vokselleri: {void}\nBoşluk rijitliği: katının {ratio} kadarı · Boşluk E: {modulus} MPa',
    '{name} must be a positive finite number.': '{name} pozitif ve sonlu olmalıdır.',
    '{name} must be positive and finite.': '{name} pozitif ve sonlu olmalıdır.',
    '{name} must be a finite number.': '{name} sonlu bir sayı olmalıdır.',
    '{name} must be a positive integer.': '{name} pozitif bir tam sayı olmalıdır.',
    '{name}: Min must be below Max.': '{name}: Min, maks değerinden küçük olmalıdır.',
    '{side} platen friction coefficient must be between 0 and 2.': '{side} plaka sürtünme katsayısı 0 ile 2 arasında olmalıdır.',
    'FE solid has {count} face-connected components. Refine the mesh.': 'SE katısı {count} yüzey bağlantılı bileşen içeriyor. Ağı inceltin.',
    'Slurm: {state} · {detail}': 'Slurm: {state} · {detail}',
    '{source}. Auto: {queue} · {cpus} CPUs · {ram} GiB. RAM maximum follows CPU count; account/QOS and job-submission rules are checked by Slurm.': '{source}. Otomatik: {queue} · {cpus} CPU · {ram} GiB. RAM üst sınırı CPU sayısına bağlıdır; hesap/QOS ve iş gönderim kurallarını Slurm denetler.',
    '{source}. RAM maximum follows CPU count; account/QOS and job-submission rules are checked by Slurm.': '{source}. RAM üst sınırı CPU sayısına bağlıdır; hesap/QOS ve iş gönderim kurallarını Slurm denetler.',
    'Pipe Reynolds ≈ {reynolds}\nNominal cell: {cell} mm. Inlet velocity is the mean over the discretized pipe inlet; pore velocity is solved.': 'Boru Reynolds ≈ {reynolds}\nNominal hücre: {cell} mm. Giriş hızı ayrıklaştırılmış boru girişinin ortalamasıdır; gözenek hızı çözülür.',
    '{count} designs': '{count} tasarım',
    '{count} reference configurations · {runs} experiments': '{count} referans yapılandırma · {runs} deney',
    '{rows} rows · {active} active\n{ready} ready · {solved} solved': '{rows} satır · {active} etkin\n{ready} hazır · {solved} çözüldü',
    'TRUBA · {host} · VPN required': 'TRUBA · {host} · VPN gerekli',
    'Min: {value}': 'Min: {value}',
    'Max: {value}': 'Maks: {value}',
    'Auto: {value}': 'Otomatik: {value}',
    'Resource session: {value}': 'Kaynak oturumu: {value}',
    'Resource session {value}': 'Kaynak oturumu {value}',
    'Analysis completed. Results: {path}': 'Analiz tamamlandı. Sonuçlar: {path}',
    'Analysis failed: {error}': 'Analiz başarısız: {error}',
    'Preview unavailable: {error}': 'Önizleme kullanılamıyor: {error}',
    'Error: {error}': 'Hata: {error}',
    'Failed: {error}': 'Başarısız: {error}',
    'TRUBA job {value}': 'TRUBA işi {value}',
    'SSH connected · {value}': 'SSH bağlı · {value}',
    'Available: {value}': 'Kullanılabilir: {value}',
    'Standard {kind}': 'Standart {kind}',
    'RustMech | {title}': 'RustMech | {title}',
    'Spacing {spacing} mm · Horizon {horizon} mm': 'Aralık {spacing} mm · Ufuk {horizon} mm',
    '{geometry} · {size} mm · {particles} particles · peak {peak} N': '{geometry} · {size} mm · {particles} parçacık · tepe kuvveti {peak} N',
    '{travel} mm · {force} N · {broken} broken bonds · max damage {damage}': '{travel} mm · {force} N · {broken} kopan bağ · en yüksek hasar {damage}',
    'TPMS compression stopped before convergence. Partial results: {folder}': 'TPMS basma analizi yakınsama sağlanmadan durdu. Kısmi sonuçlar {folder}',
    'First bond failure {force} N at {travel} mm': 'İlk bağ kopması {travel} mm deplasmanda {force} N',
    'layer {layer} mean damage {damage}': 'katman {layer} ortalama hasarı {damage}',
    'Layer {value}': 'Katman {value}',
    '{id} added to the analysis queue. Analysis has not started.': '{id} analiz kuyruğuna eklendi. Analiz başlamadı.',
    'LHS: {count} samples per structure with variables. Each range is sampled across its full span; Levels is not used.': 'LHS: Değişkenli yapı başına {count} örnek. Her aralığın tamamı örneklenir; seviye sayısı kullanılmaz.',
    'Every combination of the evenly spaced values is generated. Selected structure: {expression} = {count} experiments. Min 20, Max 40, Levels 3 → 20, 30, 40.': 'Eşit aralıklı değerlerin tüm birleşimleri oluşturulur. Seçili yapı: {expression} = {count} deney. Min 20, Maks 40, Seviye 3 → 20, 30, 40.',
}
