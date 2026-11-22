"""English source text and Turkish presentation catalog (solver identifiers stay unchanged)."""
from setup_help import GEOMETRY_HELP

_TEXT = '''
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
Characteristic grain size|Karakteristik tane boyutu
Atom count and composition counts are estimates until Atomsk builds the grain boundaries.|Atomsk tane sınırlarını oluşturana kadar atom sayıları tahminidir.
How to choose parameters|Parametreler nasıl seçilir?
The lattice parameter a is one BCC cell edge in Å. Use a value for the same alloy and phase. The initial 3.4000 Å is an example.|Kafes parametresi a, BCC birim hücresinin kenarıdır (Å). Aynı alaşım ve faz için belirlenmiş değeri kullanın. Başlangıçtaki 3,4000 Å örnek değerdir.
From the BCC (110) XRD spacing, use a = √2 × d₁₁₀. With MEAM, relax a small periodic single crystal at zero pressure. At the target temperature, average the equilibrated NPT box length and divide by the cell repeats along that axis.|XRD’deki BCC (110) düzlem aralığından a = √2 × d₁₁₀ hesaplanır. MEAM ile küçük bir periyodik tek kristali sıfır basınçta gevşetin. Hedef sıcaklıkta NPT dengelemesi sonrası ortalama kutu uzunluğunu o eksendeki hücre tekrar sayısına bölün.
BCC has two atoms per cell. For a polycrystal, atom count sets the box volume and grain count sets the average grain volume. Fix the random seed to reproduce grain positions, orientations and alloy site assignments.|BCC birim hücresinde iki atom vardır. Polikristalde hedef atom sayısı kutu hacmini, tane sayısı ise ortalama tane hacmini belirler. Tane konumlarını, yönelimlerini ve alaşım atom dağılımını tekrarlamak için aynı rastgele tohumu kullanın.
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
MEAM element order|MEAM element sırası
Example: Hf Nb Ta Ti Zr|Örnek Hf Nb Ta Ti Zr
Enter the element order used by indices in the MEAM parameter file.|MEAM parametre dosyasındaki indekslerin izlediği element sırasını yazın.
Browse…|Gözat…
MEAM files (*.meam);;All files (*)|MEAM dosyaları (*.meam);;Tüm dosyalar (*)
Preview model|Modeli önizle
Prepare and run on TRUBA|Hazırla ve TRUBA’da çalıştır
BCC supercell|BCC süper hücresi
The nearest nearly cubic BCC supercell is shown.|Hedefe en yakın, kenar tekrarları dengeli BCC hücresi gösteriliyor.
Atomsk → Minimization → Equilibration → Tension and CNA|Atomsk → Minimizasyon → Dengeleme → Çekme ve CNA
Choose the MEAM library and parameter files.|MEAM kütüphanesini ve parametre dosyasını seçin.
Generating the structure with Atomsk…|Atomsk ile yapı üretiliyor…
Model prepared. Waiting for ESEN to finish its current transfer…|Model hazır. ESEN’in mevcut aktarımının tamamlanması bekleniyor…
TRUBA connection changed. Press Prepare and run on TRUBA to submit the prepared model.|TRUBA bağlantısı değişti. Hazır modeli göndermek için Hazırla ve TRUBA’da çalıştır düğmesine basın.
ESEN is busy. Press Prepare and run on TRUBA again when it is ready.|ESEN meşgul. Hazır olduğunda Hazırla ve TRUBA’da çalıştır düğmesine tekrar basın.
Follow submission, progress and results in ESEN job history.|Gönderimi, ilerlemeyi ve sonuçları ESEN iş geçmişinden izleyin.
Choose TRUBA folder|TRUBA klasörü seç
Select an upload folder on TRUBA|TRUBA’da yükleme klasörünü seçin
TRUBA folders|TRUBA klasörleri
Use this folder|Bu klasörü kullan
Loading TRUBA folders…|TRUBA klasörleri yükleniyor…
Could not load TRUBA folders.|TRUBA klasörleri yüklenemedi.
Choose a TRUBA folder for the connected account.|Bağlı hesap için bir TRUBA klasörü seçin.
TRUBA upload folder selected. Send the prepared specimens to this folder.|Yükleme klasörü seçildi. Hazırlanan numuneleri bu klasöre gönderin.
TRUBA results folder: {path}|TRUBA sonuç klasörü: {path}
Open the LAMMPS workspace|LAMMPS çalışma alanını aç
Job settings|İş ayarları
Date modified|Değiştirilme tarihi
Job folder path|İş klasörü yolu
Previous folder|Önceki klasör
Next folder|Sonraki klasör
Copy ESEN file preparation instructions for AI|ESEN dosya hazırlama yönergesini yapay zekâ için kopyala
ESEN instructions copied to the clipboard.|ESEN yönergesi panoya kopyalandı.
Advanced TRUBA settings|Gelişmiş TRUBA ayarları
This computer|Bu bilgisayar
Run on|Çalıştırma hedefi
MPI processes|MPI süreçleri
Local CPU cores|Yerel CPU çekirdeği
Local RAM limit (MiB)|Yerel RAM sınırı (MiB)
No limit|Sınırsız
Total memory limit across local MPI processes.|Yerel MPI süreçlerinin toplam bellek sınırı.
Preview values are estimates. Slurm limits apply at submission.|Önizleme tahminidir. Gönderimde Slurm sınırları geçerlidir.
The selected TRUBA queue is unavailable.|Seçilen TRUBA kuyruğu kullanılamıyor.
Local memory limits are available on Windows.|Yerel bellek sınırı Windows’ta kullanılabilir.
LAMMPS executable|LAMMPS programı
MPI launcher|MPI başlatıcısı
Memory (MiB)|Bellek (MiB)
Wall time (HH:MM:SS)|Süre (SS:DD:SS)
LAMMPS module|LAMMPS modülü
Linux executable|Linux programı
Input script…|Girdi betiği…
Job folder…|İş klasörü…
Add Folder|Klasör Ekle
Select Folder|Klasör Seç
Scan folder for jobs|Klasördeki işleri tara
Set job folder for selected files|Seçili dosyalar için iş klasörünü ayarla
Upload to TRUBA|TRUBA’ya Aktar
Upload inputs to the open TRUBA folder|Girdileri açık TRUBA klasörüne doğrudan aktar
Sync|Eşitle
Sync the local folder with the open TRUBA subfolder|Yerel klasörü açık TRUBA alt klasörüyle eşitle
Syncing with {path}…|{path} ile eşitleniyor…
Folders synced. Uploaded: {sent} · Downloaded: {received}|Klasörler eşitlendi. Gönderilen: {sent} · İndirilen: {received}
Folder sync conflict: {path}|Klasör eşitleme çakışması: {path}
Folder sync failed: {error}|Klasör eşitleme başarısız: {error}
A file or folder with different contents already exists: {path}|Aynı konumda farklı içerikli bir dosya veya klasör var: {path}
Upload the folder to TRUBA before running selected scripts.|Seçtiğiniz betikleri çalıştırmadan önce klasörü TRUBA’ya aktarın.
Use ↑ and ↓ in Job order. Run syncs the inputs and sends each checked Slurm script as a separate job.|İş sırası görünümünde ↑ ve ↓ ile sırayı ayarlayın. Çalıştır, girdileri eşitler ve işaretli her Slurm betiğini ayrı iş olarak gönderir.
Run submits the checked jobs that have not been sent to Slurm yet.|Çalıştır, henüz Slurm'a gönderilmemiş işaretli işleri gönderir.
Uploading files to TRUBA…|Dosyalar TRUBA’ya aktarılıyor…
TRUBA file transfer progress|TRUBA dosya aktarımı ilerlemesi
Preparing files for TRUBA…|Dosyalar TRUBA için hazırlanıyor…
Uploading to TRUBA · %p%|TRUBA’ya aktarılıyor · %p%
Finalizing TRUBA transfer…|TRUBA aktarımı tamamlanıyor…
TRUBA transfer complete · %p%|TRUBA aktarımı tamamlandı · %p%
TRUBA transfer failed|TRUBA aktarımı başarısız
Stage: {stage}|Aşama: {stage}
Runtime: {time}|Çalışma süresi: {time}
Slurm allocation runtime: {time}|Slurm tahsisinin çalışma süresi: {time}
Last reported step: {step}|Son bildirilen adım: {step}
Collecting results|Sonuçlar alınıyor
Uploaded to TRUBA: {path}|TRUBA’ya aktarıldı: {path}
File upload failed: {error}|Dosyalar aktarılamadı: {error}
Files|Dosyalar
Script|Betik
Root|Kök Klasör
Remove|Çıkar
Reuse|Girdiyi Al
Resume|Sürdür
Attach|Bağla
Download|İndir
Choose an existing folder.|Mevcut bir klasör seçin.
Select files or a single folder.|Dosya veya tek bir klasör seçin.
Select a script to run.|Çalıştırmak için bir betik seçin.
Select a script in the TRUBA file browser.|TRUBA dosya tarayıcısında bir betik seçin.
Select a Slurm or LAMMPS input script on TRUBA.|TRUBA’da bir Slurm veya LAMMPS girdi betiği seçin.
Submit the selected TRUBA Slurm script.|Seçilen TRUBA betiğini Slurm’a gönder.
Submit every Slurm script listed in Run.esen.|Run.esen dosyasındaki tüm Slurm betiklerini gönder.
Reading Run.esen from TRUBA…|Run.esen dosyası TRUBA’dan okunuyor…
TRUBA connection changed. Run the list again.|TRUBA bağlantısı değişti. Listeyi yeniden çalıştırın.
{count} scripts prepared from {folder}.|{folder} klasöründe {count} betik gönderim için hazırlandı.
The TRUBA run list is too large.|TRUBA iş listesi çok büyük.
The ESEN run list is empty.|ESEN iş listesi boş.
Run uploads the selected local inputs to TRUBA.|Çalıştır, seçili yerel girdileri TRUBA’ya aktarır.
Add a folder or drop inputs here.|Klasör ekleyin veya girdileri buraya bırakın.
{files} files · {scripts} scripts selected|{files} dosya · {scripts} betik seçili
Jobs per script|Betik başına iş
Manual|Elle
Automatically include all runnable scripts in the folder.|Klasördeki çalıştırılabilir betikleri otomatik olarak dahil eder.
No runnable scripts found.|Çalıştırılabilir betik bulunamadı.
{count} jobs found · up to {maximum} per Slurm script|{count} iş bulundu · Slurm betiği başına en fazla {maximum} iş
Grouped jobs run in sequence and share one Slurm time limit.|Bir gruptaki işler sırayla çalışır ve aynı Slurm süre sınırını paylaşır.
Slurm scripts with different resource settings run in separate groups.|Farklı kaynak ayarları olan Slurm betikleri ayrı gruplarda çalışır.
The Slurm scripts no longer share the same resource settings.|Slurm betiklerinin kaynak ayarları artık aynı değil.
Jobs with a restart input run separately.|Devam girdisi olan işler ayrı çalışır.
Group of {count} jobs|{count} işlik grup
Scanning folder…|Klasör taranıyor…
No LAMMPS input scripts named in.* or *.in were found.|in.* veya *.in adında LAMMPS girdi betiği bulunamadı.
Waiting to run|Çalıştırma sırasını bekliyor
Choose a job folder outside the ESEN history directory.|ESEN iş geçmişini içermeyen bir iş klasörü seçin.
Load files…|Dosya yükle…
LAMMPS input files|LAMMPS girdi dosyaları
Include entire job folder|İş klasörünün tamamını ekle
Filter|Filtre
Filter by name or extension|Ada veya uzantıya göre filtrele
Filter input files|Girdi dosyalarını filtrele
Checked files stay checked when hidden by the filter.|Filtreyle gizlenen dosyaların işareti korunur.
Sort input files|Girdi dosyalarını sırala
For manual Slurm ordering, select Job order and clear the filter.|Slurm betiklerini elle sıralamak için İş sırası'nı seçip filtreyi temizleyin.
Job order|İş sırası
Name (A–Z)|Ada göre (A–Z)
Format (A–Z)|Biçime göre (A–Z)
Remove file|Dosyayı çıkar
Remove selected files|Seçili dosyaları çıkar
Use Ctrl or Shift to select files. Press Space to check or uncheck selected scripts.|Ctrl veya Shift ile birden fazla dosya seçin. Boşluk tuşuyla seçili betiklerin işaretini değiştirin.
Use As Script|Betik Olarak Seç
Run LAMMPS|LAMMPS'i çalıştır
Job history|İş geçmişi
Show only active jobs|Sadece aktif işleri göster
ESEN job history|ESEN iş geçmişi
Remove job from history|İşi geçmişten sil
Remove the selected job from history? Job files and results will be kept.|Seçili iş geçmişten silinsin mi? İş dosyaları ve sonuçlar korunacak.
Remove {count} selected jobs from history? Active jobs, job files and results will be kept.|Seçili {count} iş geçmişten silinsin mi? Aktif işler, iş dosyaları ve sonuçlar korunacak.
Job became active: {name}|İş aktif duruma geçti: {name}
Clear reuse|Yeniden kullanımı temizle
{count} jobs ready to reuse. Run queues them on their saved targets.|{count} iş yeniden kullanıma hazır. Çalıştır, işleri kayıtlı hedeflerinde sıraya alır.
Reuse unavailable for: {names}|Yeniden kullanılamayan işler: {names}
Move script up|Betiği yukarı taşı
Move script down|Betiği aşağı taşı
Reuse inputs|Girdileri yeniden kullan
Refresh job|İşi yenile
Resume job|İşi sürdür
Restart input|Devam girdisi
Optional relative path|İsteğe bağlı göreli yol
Include a restart input in the job files to enable checkpoint recovery and the 70-hour handover.|Ara kayıttan devam etmek ve 70. saatte devri açmak için devam betiğini iş dosyalarına ekleyin.
Include the LAMMPS restart input in the job files.|LAMMPS devam betiğini iş dosyalarına ekleyin.
TRUBA run folder|TRUBA koşu klasörü
Attach folder|Klasörü ekle
Waiting for results|Sonuç bekleniyor
Results available|Sonuçlar hazır
Automatic result download failed. Use Refresh job to retry.|Sonuçlar otomatik indirilemedi. Tekrar denemek için İşi yenile düğmesini kullanın.
Choose a TRUBA run folder under your scratch directory.|Scratch alanınızdaki bir TRUBA koşu klasörünü seçin.
Stop job|İşi durdur
Command preview|Komut önizlemesi
TRUBA files|TRUBA dosyaları
Filter TRUBA files|TRUBA dosyalarını filtrele
Group by type|Türe göre grupla
Folders|Klasörler
Scripts|Betikler
Other files|Diğer dosyalar
TRUBA folder path|TRUBA klasör yolu
Open selected TRUBA folder|Seçili TRUBA klasörünü aç
Slurm inputs|Slurm girdileri
Open synchronized Slurm inputs on TRUBA|TRUBA’ya eşitlenen Slurm girdilerini aç
Synchronized inputs are read-only.|Eşitlenen girdiler salt okunurdur.
Upload|Yükle
Upload file to the open TRUBA folder|Açık TRUBA klasörüne dosya yükle
Download selected TRUBA file|Seçili TRUBA dosyasını indir
Select all files|Tüm dosyaları seç
Select visible TRUBA files (Ctrl+A)|Görünen TRUBA dosyalarını seç (Ctrl+A)
Delete selected TRUBA files (Delete)|Seçili TRUBA dosyalarını sil (Delete)
Delete TRUBA files|TRUBA dosyalarını sil
Deleting selected TRUBA files…|Seçili TRUBA dosyaları siliniyor…
Could not delete the selected TRUBA files.|Seçili TRUBA dosyaları silinemedi.
Select regular files to delete.|Silmek için normal dosyaları seçin.
File changed since it was listed.|Dosya listelendikten sonra değişti.
Could not delete the file.|Dosya silinemedi.
Upload file to TRUBA|TRUBA'ya dosya yükle
Download file from TRUBA|TRUBA'dan dosya indir
Uploading file to TRUBA…|Dosya TRUBA'ya yükleniyor…
Downloading file from TRUBA…|Dosya TRUBA'dan indiriliyor…
File uploaded to TRUBA.|Dosya TRUBA'ya yüklendi.
File downloaded from TRUBA.|Dosya TRUBA'dan indirildi.
Could not upload the file to TRUBA.|Dosya TRUBA'ya yüklenemedi.
Could not download the file from TRUBA.|Dosya TRUBA'dan indirilemedi.
A file or folder with that name already exists on TRUBA.|TRUBA'da bu adda bir dosya veya klasör zaten var.
Select a regular file to download.|İndirmek için normal bir dosya seçin.
Up|Üst klasör
Folder name|Klasör adı
New folder|Yeni klasör
Loading TRUBA files…|TRUBA dosyaları yükleniyor…
Creating folder…|Klasör oluşturuluyor…
This folder is empty.|Bu klasör boş.
Enter a single folder name.|Tek bir klasör adı girin.
This folder is outside the RustMech project.|Bu klasör RustMech projesinin dışında.
TRUBA folder was not found.|TRUBA klasörü bulunamadı.
The last TRUBA folder is unavailable. Opened the project folder.|Son TRUBA klasörüne erişilemedi. Proje klasörü açıldı.
Permission denied on TRUBA.|TRUBA'da erişim reddedildi.
Could not load TRUBA files.|TRUBA dosyaları yüklenemedi.
Could not create the folder. Check if the name already exists.|Klasör oluşturulamadı. Bu adın zaten kullanılıp kullanılmadığını kontrol edin.
Logs and files|Günlükler ve dosyalar
Queued and running jobs|Kuyruktaki ve çalışan işler
Standard output|Standart çıktı
Standard error|Standart hata
Job log tail|İş günlüğünün son satırları
Result files|Sonuç dosyaları
Open file|Dosyayı aç
Open File|Dosyayı Aç
Open job folder|İş klasörünü aç
Download file|Dosyayı indir
LAMMPS input script|LAMMPS girdi betiği
Job folder|İş klasörü
Job Folder|İş Klasörü
Load input files|Girdi dosyalarını yükle
Load files or drop them on the input list.|Dosyaları yükleyin veya girdi listesine sürükleyip bırakın.
Select the input file and click Use As Script.|Girdi betiğini seçip Betik Olarak Seç düğmesine basın.
Load existing files. To include a folder, use Job folder.|Mevcut dosyaları yükleyin. Klasör eklemek için İş klasörü düğmesini kullanın.
Choose files inside the job folder.|İş klasörünün içinden dosya seçin.
Choose a LAMMPS input script.|Bir LAMMPS girdi betiği seçin.
The selected input script is missing.|Seçilen girdi betiği bulunamadı.
Connect using the main TRUBA button.|Ana penceredeki TRUBA düğmesinden bağlanın.
Windows paths found in the script. Use relative paths with / on TRUBA.|Betikte Windows dosya yolu bulundu. TRUBA için / ile göreli yollar kullanın.
Select an ESEN job.|Bir ESEN işi seçin.
Some files are omitted from this list.|Bazı dosyalar bu listede gösterilmiyor.
Submitting|Gönderiliyor
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
+ Value column|+ Değer sütunu
- Last column|- Son sütun
Build matrix|Matrisi oluştur
Add to DoE matrix|DoE matrisine ekle
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
Structure family|Yapı ailesi
Structure type|Yapı türü
Structure / Dimensions|Yapı / Ölçüler
Family|Aile
Morphology|Morfoloji
Dimensions|Ölçüler
Size|Boyut
Size (mm)|Boyut (mm)
Cube size|Küp boyutu
Cube size (mm)|Küp boyutu (mm)
Cell size|Hücre boyutu
Cell Size (mm)|Hücre boyutu (mm)
Cell size (mm)|Hücre boyutu (mm)
Cell / Pore (mm)|Hücre / Gözenek (mm)
Density|Yoğunluk
Density (%)|Yoğunluk (%)
Relative density (%)|Bağıl yoğunluk (%)
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
Global mesh size (mm)|Genel ağ boyutu (mm)
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
Geometry-trimmed voxels (HEX8)|Geometriye göre kırpılmış vokseller (HEX8)
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
Compression (mm)|Basma (mm)
Compression / mm|Basma / mm
Compression force (N)|Basma kuvveti (N)
Compression force / N|Basma kuvveti / N
Actuator travel|Aktüatör hareketi
Actuator travel (mm)|Aktüatör hareketi (mm)
Final strain (%)|Son şekil değiştirme (%)
Final nominal strain (%)|Son nominal şekil değiştirme (%)
Target strain (%)|Hedef şekil değiştirme (%)
Common final strain (%)|Ortak son şekil değiştirme (%)
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
Flow CFD|Akış CFD
Flow (CFD)|Akış (CFD)
Thermal CHT|Isıl CHT
Thermal (CHT)|Isıl (CHT)
Transient Heat Transfer|Geçici ısı transferi
Designs|Tasarımlar
Batch Analysis|Toplu Analiz
Batch analysis from folder...|Klasörden toplu analiz...
Design|Tasarım
Design Pool|Tasarım havuzu
Design Pool (Gitterburg)...|Tasarım havuzu (Gitterburg)...
Design Pool - Gitterburg / RustMech|Tasarım havuzu - Gitterburg / RustMech
Design Pool and analysis queue|Tasarım havuzu ve analiz kuyruğu
Analysis queue|Analiz kuyruğu
Analysis queue...|Analiz kuyruğu...
Analysis Queue|Analiz Kuyruğu
Add to Queue|Kuyruğa Ekle
Send to TRUBA|TRUBA'ya Gönder
Selection or settings changed. Send the batch to TRUBA again.|Seçim veya ayarlar değişti. Toplu analizi TRUBA'ya yeniden gönderin.
ready. Review the faces and select the specimens to analyze.|hazır. Yüzleri kontrol edin ve analiz edilecek numuneleri seçin.
Uploading geometry and analysis settings to TRUBA…|Geometri ve analiz ayarları TRUBA'ya yükleniyor…
Checking specimen files and analysis settings…|Numune dosyaları ve analiz ayarları kontrol ediliyor…
Geometry and analysis settings uploaded to TRUBA. Select Run to start.|Geometri ve analiz ayarları TRUBA'ya yüklendi. Başlatmak için Çalıştır'a tıklayın.
Batch analysis running…|Toplu analiz çalışıyor…
Completed {completed}/{total} analyses.|{completed}/{total} analiz tamamlandı.
Use the TRUBA account that received the prepared specimens.|Hazırlanan numunelerin gönderildiği TRUBA hesabını kullanın.
Select all ready specimens|Hazır numunelerin tümünü seç
Upload prepared specimens, then add the jobs to the analysis queue.|Hazırlanan numuneleri yükle ve işleri analiz kuyruğuna ekle.
Prepared geometry uploaded to BatchInputs on TRUBA.|Hazırlanan geometri TRUBA'daki BatchInputs klasörüne yüklendi.
Previously queued job. Geometry uploads when the queue runs.|Önceden kuyruğa eklenen iş. Geometri kuyruk çalıştırıldığında yüklenir.
TRUBA is disconnected. Connect using the TRUBA button, then send the prepared specimens.|TRUBA bağlantısı yok. TRUBA düğmesinden bağlanın, ardından hazırlanan numuneleri gönderin.
Checking and uploading prepared specimens to TRUBA…|Hazırlanan numuneler kontrol edilip TRUBA'ya yükleniyor…
Wait for the TRUBA upload to finish.|TRUBA yüklemesinin bitmesini bekleyin.
ready. Edit the faces on the left, then select the specimens to send or queue.|hazır. Solda yüzeyleri düzenleyin, ardından gönderilecek veya kuyruğa eklenecek numuneleri seçin.
Mesh and analysis run on TRUBA with Kratos. Self-contact is disabled. TET10 uses displacement control and penalty mortar. Material and loading come from the Mechanic workspace.|Ağ üretimi ve analiz TRUBA'da Kratos ile çalışır. Öz temas devre dışıdır. TET10 yer değiştirme kontrolü ve ceza tabanlı mortar kullanır. Malzeme ve yükleme Mekanik çalışma alanından alınır.
Check at least one ready specimen.|Hazır numunelerden en az birini işaretleyin.
Checking source files before saving jobs to Analysis Queue…|İşler Analiz Kuyruğuna kaydedilmeden önce kaynak dosyalar kontrol ediliyor…
The selected jobs are already in Analysis Queue.|Seçili işler zaten Analiz Kuyruğunda.
Source changed after preparation. Check the folder again.|Kaynak dosya hazırlıktan sonra değişti. Klasörü yeniden kontrol edin.
Execution|Yürütme
Direction|Yön
Choose the TRUBA queue for the generated Slurm script.|Oluşturulacak Slurm betiği için TRUBA kuyruğunu seçin.
Execution target|Yürütme hedefi
Install local Kratos from the Kratos settings before running this analysis.|Bu analizi çalıştırmadan önce Kratos ayarlarından yerel Kratos'u kurun.
Install local Kratos|Yerel Kratos'u Kur
Installing and checking Kratos…|Kratos kuruluyor ve doğrulanıyor…
Local Kratos installation is available on Windows.|Yerel Kratos Windows'ta kurulabilir.
Local Kratos requires 64-bit Python 3.14 and an internet connection for setup.|Yerel Kratos kurulumu için 64 bit Python 3.14 ve internet bağlantısı gerekir.
Ready to submit analysis to TRUBA.|Analiz TRUBA'ya gönderilmeye hazır.
Local|Yerel
Local mesh|Yerel ağ üretimi
TRUBA batch analysis|TRUBA toplu analizi
Enable TRUBA mode and allocate or refresh a resource session before running these jobs.|Bu işleri çalıştırmadan önce TRUBA modunu açın ve bir kaynak oturumu ayırın veya mevcut oturumu yenileyin.
Mesh and solution run locally. Analysis settings come from the Mechanic workspace.|Ağ ve çözüm yerelde çalışır. Analiz ayarları Mekanik çalışma alanından alınır.
Material, loading, contact and solver parameters are captured from the main Mechanic workspace when checking.|Malzeme, yükleme, temas ve çözücü ayarları ön kontrol sırasında ana Mekanik çalışma alanından alınır.
Remove from Queue|Kuyruktan Çıkar
Selected job removed from the queue.|Seçili iş kuyruktan çıkarıldı.
Stop the analysis queue before removing jobs.|İşleri çıkarmadan önce analiz kuyruğunu durdurun.
Specimens|Numuneler
Specimen|Numune
Preflight|Ön Kontrol
Analysis queue - RustMech|Analiz kuyruğu - RustMech
Add current model to analysis queue|Mevcut modeli analiz kuyruğuna ekle
Configure selected...|Seçileni yapılandır...
1. New geometry|1. Yeni geometri
2. Saved designs|2. Kaydedilen tasarımlar
3. Selected design / variables|3. Seçili tasarım / Değişkenler
Design experiments|Deney tasarla
Experiment matrix|Deney matrisi
Experiment variables|Deney değişkenleri
Machine learning|Makine öğrenmesi
Machine learning matrix|Makine öğrenmesi matrisi
DoE experiment matrix|DoE deney matrisi
Design · Size / Cell / Density|Tasarım · Boyut / Hücre / Yoğunluk
Vary|Değişken
Min|Min
Max|Maks
Levels|Seviye
Sampling|Örnekleme
Full factorial grid|Tam faktöriyel
Latin hypercube (LHS)|Latin hiperküp (LHS)
Runs per design|Tasarım başına deney
LHS samples per geometry|Geometri başına LHS örneği
Copy from design|Tasarımdan al
Min from…|Min tasarımı…
Max from…|Maks tasarımı…
Generate experiments|Deneyleri oluştur
Generate ML matrix|ML matrisi oluştur
Preview bounds|Sınırları göster
Preview in 3D|3B önizle
Geometry library|Geometri kitaplığı
Geometry preview|Geometri önizlemesi
Geometry · library preview|Geometri · Kitaplık önizlemesi
Include geometry|Geometriyi ekle
Remove selected|Seçileni kaldır
Untitled plan|Adsız plan
Open DoE plan|DoE planını aç
Save DoE plan|DoE planını kaydet
Cannot open DoE plan|DoE planı açılamadı
Cannot save DoE plan|DoE planı kaydedilemedi
Unsaved DoE plan|Kaydedilmemiş DoE planı
Table data|Tablo verileri
Copy for Excel|Excel için kopyala
Import CSV|CSV içe aktar
Export CSV|CSV dışa aktar
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
Dataset split|Veri kümesi bölümü
Geometry group|Geometri grubu
Run folder|Çalışma klasörü
Results|Sonuçlar
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
Save and close|Kaydet ve kapat
Specimen faces|Numune yüzeyleri
Edit specimen geometry and faces|Numune geometrisini ve yüzeylerini düzenle
Face preview unavailable|Yüzey önizlemesi kullanılamıyor
Support face|Mesnet yüzeyi
Compression face|Basma yüzeyi
Connected components|Bağlantılı bileşenler
Choose one solid to keep. Blue remains, red is excluded.|Kalacak katıyı seçin. Mavi bileşen kalır, kırmızı bileşenler ayıklanır.
Choose which connected component to keep.|Kalacak bağlantılı bileşeni seçin.
Multiple substantial components. Choose which solid to keep in Edit.|Birden fazla büyük bileşen var. Düzenle penceresinde kalacak katıyı seçin.
No planar end face at the specimen boundary; select faces manually or repair the end geometry.|Numune sınırında düz bir uç yüzey bulunamadı. Yüzeyleri elle seçin veya uç geometrisini onarın.
Component {index}|Bileşen {index}
Inspect / select faces|Yüzeyleri incele / seç
Specimen faces saved.|Numune yüzeyleri kaydedildi.
Could not save the specimen faces.|Numune yüzeyleri kaydedilemedi.
Support: green. Compression: orange. Faces validated.|Mesnet: yeşil. Yükleme: turuncu. Yüzeyler doğrulandı.
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
Fixed platen: green. Moving platen: orange.|Sabit tabla: yeşil. Hareketli tabla: turuncu.
Orthographic|Ortografik
Perspective|Perspektif
Smooth geometry|Pürüzsüz geometri
Smooth + FE overlay|Geometri + SE üst üste
Overlay|Üst üste
Top|Üst
Front|Ön
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
No saved TRUBA credentials. Open TRUBA to enter and remember your credentials.|Kayıtlı TRUBA kimlik bilgisi yok. Bilgilerinizi girip kaydetmek için TRUBA’yı açın.
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
Building smooth geometry…|Pürüzsüz geometri oluşturuluyor…
Building FE mesh in background…|SE ağı arka planda oluşturuluyor…
Updating geometry…|Geometri güncelleniyor…
Updating geometry preview…|Geometri önizlemesi güncelleniyor…
Generating geometry in background...|Geometri arka planda üretiliyor...
Preparing geometry preview...|Geometri önizlemesi hazırlanıyor...
Preparing geometry preview…|Geometri önizlemesi hazırlanıyor…
The selected analysis method is not available.|Seçilen analiz yöntemi kullanılamıyor.
Allocate or refresh the resource session and wait until it is ready before computing.|Kaynak oturumunu ayırın veya yenileyin ve hesaplamadan önce hazır olmasını bekleyin.
TRUBA runtime is being checked and prepared automatically. Wait until READY before computing.|TRUBA çalışma ortamı otomatik olarak denetleniyor ve hazırlanıyor. Hesaplamadan önce hazır olmasını bekleyin.
TRUBA runtime preparation did not complete.|TRUBA çalışma ortamının hazırlığı tamamlanmadı.
Settings changed — updating geometry preview...|Ayarlar değişti — Geometri önizlemesi güncelleniyor...
Cancelling mesh generation…|Ağ üretimi iptal ediliyor…
Smooth geometry · Ready|Pürüzsüz geometri · Hazır
Ready · uses loading and contact conditions from the main setup|Hazır · Ana ayarlardaki yükleme ve temas koşullarını kullanır
Geometry or mesh settings have changed. Click Apply to update the preview.|Geometri veya ağ ayarları değişti. Önizlemeyi güncellemek için Uygula'ya tıklayın.
Export to ANSYS|ANSYS'e Aktar
Save the generated FE mesh in an ANSYS-compatible format.|Oluşturulan sonlu eleman ağını ANSYS uyumlu bir biçimde kaydet.
ANSYS mesh (*.cdb)|ANSYS ağı (*.cdb)
Abaqus input (*.inp)|Abaqus girdi dosyası (*.inp)
Nastran bulk data (*.bdf)|Nastran veri dosyası (*.bdf)
Nastran bulk data (*.dat)|Nastran veri dosyası (*.dat)
Nastran bulk data (*.nas)|Nastran veri dosyası (*.nas)
Fluent mesh (*.msh)|Fluent ağı (*.msh)
Fluent case (*.cas)|Fluent vaka dosyası (*.cas)
Workbench mesh (*.acmo)|Workbench ağı (*.acmo)
ICEM CFD mesh (*.uns)|ICEM CFD ağı (*.uns)
LS-DYNA keyword (*.k)|LS-DYNA girdi dosyası (*.k)
LS-DYNA keyword (*.key)|LS-DYNA girdi dosyası (*.key)
Voxel-derived solid elements for mesh export.|Dışa aktarma için vokselden türetilen katı elemanlar.
Mixed hex and pyramid cells for mesh export.|Dışa aktarma için karma altıyüzlü ve piramit elemanlar.
Exporting mesh to ANSYS…|Ağ ANSYS'e aktarılıyor…
ANSYS mesh export failed|ANSYS ağı dışa aktarılamadı
ANSYS mesh saved to|ANSYS ağı kaydedildi
Geometry preview unavailable.|Geometri önizlemesi kullanılamıyor.
Geometry is empty; adjust its parameters.|Geometri boş; parametreleri düzenleyin.
Plan opened. Click Apply to preview geometry.|Plan açıldı. Geometriyi önizlemek için Uygula'ya tıklayın.
Dimensions changed. Click Apply to generate geometry.|Ölçüler değişti. Geometriyi üretmek için Uygula'ya tıklayın.
Add geometry → select a saved design → choose variables and their limits.|Geometri ekle → Kayıtlı tasarımı seç → Değişkenleri ve sınırlarını belirle.
Checked variables apply to this geometry group. Enter limits or copy them from a saved design.|İşaretli değişkenler bu geometri grubuna uygulanır. Sınırları girin veya kayıtlı tasarımdan alın.
Select a saved design to edit its dimensions.|Ölçülerini düzenlemek için kayıtlı bir tasarım seçin.
Add a geometry, then select it here to edit dimensions and variables.|Bir geometri ekleyin; ölçülerini ve değişkenlerini düzenlemek için buradan seçin.
Add a geometry and check the saved designs to include in your experiment plan.|Bir geometri ekleyin ve deney planına dahil edilecek tasarımları işaretleyin.
Add the current structure and dimensions to DoE without generating an FE mesh.|SE ağı üretmeden mevcut yapıyı ve ölçüleri DoE planına ekleyin.
Generate the geometry preview using the entered structure and dimensions.|Girilen yapı ve ölçülerle geometri önizlemesini oluşturun.
Opening this file replaces your unsaved plan. Continue?|Bu dosya kaydedilmemiş planınızın yerine açılacak. Devam edilsin mi?
Wait for the current batch to finish or stop it before opening a plan.|Plan açmadan önce toplu işin bitmesini bekleyin veya durdurun.
Wait for the current batch to finish or stop it before saving the plan.|Planı kaydetmeden önce toplu işin bitmesini bekleyin veya durdurun.
Replace experiments?|Deneyler değiştirilsin mi?
Generating a new plan replaces the current table.|Yeni plan oluşturmak mevcut tablonun yerine geçer.
No Pending Rows|Bekleyen satır yok
All checked rows are already marked Completed, or no rows are checked.|İşaretli tüm satırlar tamamlanmış veya hiçbir satır işaretlenmemiş.
No design selected.|Seçili tasarım yok.
Choose a geometry to preview.|Önizlemek için geometri seçin.
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
Open Design of Experiments settings.|Deney tasarımı ayarlarını açın.
Select settings method|Ayar yöntemini seçin
Select workspace|Çalışma alanını seçin
Select a method to open its settings|Ayarlarını açmak için bir yöntem seçin
Drag to rotate · Scroll to zoom · Middle-drag to pan|Döndürmek için sürükle · Yakınlaştırmak için tekerleği çevir · Kaydırmak için orta tuşla sürükle
Toggle perspective projection (OFF: orthographic view)|Perspektifi aç/kapat (kapalı: ortografik görünüm)
3D display mode|3B görüntüleme modu
View from above (XY)|Üstten görünüm (XY)
View from the front (XZ)|Önden görünüm (XZ)
Reset to the isometric comparison view|İzometrik karşılaştırma görünümüne dön
Lowest value included in this variable's range.|Bu değişkenin aralığına dahil en küçük değer.
Highest value included in this variable's range.|Bu değişkenin aralığına dahil en büyük değer.
Number of evenly spaced values, including Min and Max. For example: Min 20, Max 40, Levels 3 gives 20, 30, 40. LHS uses Runs per design instead.|Min ve maks dahil eşit aralıklı değer sayısı. Örnek: Min 20, Maks 40, Seviye 3 → 20, 30, 40. LHS bunun yerine tasarım başına deney sayısını kullanır.
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
Case|Senaryo
Checking geometry and transfer integrity...|Geometri ve aktarım bütünlüğü denetleniyor...
Coarse to fine, e.g. 0.3, 0.2, 0.15 (mm)|Kabadan inceye, ör. 0.3, 0.2, 0.15 (mm)
Coordinates are mm. Select two opposite parallel planar end faces. Right-click a face to select it.|Koordinatlar mm'dir. Karşılıklı iki paralel düz uç yüzeyi seçin. Seçmek için yüzeye sağ tıklayın.
Deformation ×|Deformasyon ×
Displayed results use this saved case. A new run uses the current Model workspace geometry and the flow inputs at left.|Görüntülenen sonuçlar bu kayıtlı senaryoya aittir. Yeni çalışma, mevcut Model geometrisini ve soldaki akış girdilerini kullanır.
DoE preview case|DoE önizleme senaryosu
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
Rotate to inspect. Include this geometry to configure its mesh and variables.|İncelemek için döndürün. Ağını ve değişkenlerini ayarlamak için geometriyi ekleyin.
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
Geometry-trimmed voxel mesh|Geometriye göre kırpılmış voksel ağı
Geo. Voxel|Geo. Voksel
Planned|Planlanıyor
Material Point Method|Malzeme noktası yöntemi
Peridynamics|Peridinamik
FFT Homogenization|FFT homojenleştirme
Voxels per axis|Eksen başına voksel
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
Large-deformation platen compression and impact.|Büyük deformasyonlu plaka basması ve darbe.
Clip boundary voxels against the reference geometry. Cut-cell element types, small cells and ANSYS compatibility require validation.|Sınır voksellerini referans geometriye göre kırpar. Kesik hücre eleman türleri, küçük hücreler ve ANSYS uyumluluğu doğrulama gerektirir.
Bilinear approximation: E, true yield stress and Eₜ. Choose the true stress–strain curve when measured hardening data is available.|Çift doğrulu yaklaşım: E, gerçek akma gerilmesi ve Eₜ. Ölçülmüş pekleşme verisi varsa gerçek gerilme–şekil değiştirme eğrisini seçin.
Uses each geometry's selected variables. Without variables, only its reference case is generated.|Her geometrinin seçili değişkenlerini kullanır. Değişken yoksa yalnız referans deneyi oluşturulur.
1 reference case|1 referans deneyi
Top platen displacement in −Z; reaction force is calculated (N).|Üst plakanın −Z yönündeki yer değiştirmesi; tepki kuvveti hesaplanır (N).
Pending / active remote jobs|Bekleyen / Etkin uzak işler
TRUBA jobs|TRUBA işleri
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
    'Add selects this material. Analysis does not start.': 'Ekle, bu malzemeyi seçer. Analiz başlatılmaz.',
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
    'Saved to Analysis Queue ({count}). Open Analysis Queue and select Run analysis queue to start.':
        'Analiz Kuyruğuna {count} iş kaydedildi. Başlatmak için Analiz Kuyruğunu açıp Analiz kuyruğunu çalıştır düğmesine basın.',
    'Results folder is {path}': 'Sonuç klasörü {path}',
    'Ready | height {height} mm | Excluded components: {count} | End faces flattened': 'Hazır | yükseklik {height} mm | Ayıklanan bileşen: {count} | Uç yüzeyler düzleştirildi',
    'Ready | height {height} mm | End faces flattened': 'Hazır | yükseklik {height} mm | Uç yüzeyler düzleştirildi',
    'Ready | height {height} mm': 'Hazır | yükseklik {height} mm',
    'Ready | height {height} mm | Excluded components: {count}': 'Hazır | yükseklik {height} mm | Ayıklanan bileşen: {count}',
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
    '{id} added to the analysis queue. Analysis has not started.': '{id} analiz kuyruğuna eklendi. Analiz başlamadı.',
    'LHS: {count} samples per structure with variables. Each range is sampled across its full span; Levels is not used.': 'LHS: Değişkenli yapı başına {count} örnek. Her aralığın tamamı örneklenir; seviye sayısı kullanılmaz.',
    'Every combination of the evenly spaced values is generated. Selected structure: {expression} = {count} experiments. Min 20, Max 40, Levels 3 → 20, 30, 40.': 'Eşit aralıklı değerlerin tüm birleşimleri oluşturulur. Seçili yapı: {expression} = {count} deney. Min 20, Maks 40, Seviye 3 → 20, 30, 40.',
}
