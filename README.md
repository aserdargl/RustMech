# RustMech

## Güncel durum ve çevrimdışı testler

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

FFT Homogenization, Material Point Method ve Peridynamics menü sayfaları yalnız
plan açıklamalarıdır. Kırpılmış voxel mesh planı **ertelendi; şimdilik
uygulanmayacak**. Bunlar mevcut çözüm yöntemleri olarak değerlendirilmemelidir.

## İsteğe bağlı TRUBA hesaplama modu

TRUBA panelindeki **Run mesh generation, convergence and analyses on TRUBA**
seçeneği, ağır sayısal işleri SLURM hesaplama düğümlerine yönlendirir. Bilgisayar
yalnız ayarları, iş durumunu ve indirilen VTK/CSV verilerinin yerel görüntüsünü
yönetir. Bu seçenek **her uygulama açılışında kapalıdır**. Paneli açmak veya
SSH bağlantısını kurmak hesaplama modunu ya da bir analizi kendiliğinden başlatmaz.

1. `.\Run-RustMech.ps1` ile kaynak sürümünü açın. **TRUBA** düğmesinden kendi
   hesabınızla Connect yapın; mevcut kayıtlı WinSCP TRUBA oturumu da kullanılabilir.
2. Uzak çalışma seçeneklerinde kuyruk, hesap, süre, bellek ve CPU sayısını girin.
   Python modülü, ayrı çalışma ortamının Python yolu ve OpenCFD modülü kullanıcıya
   özel saklanır. Varsayılan OpenCFD modül adı geçmiş kurulumla uyumludur;
   gerçekten yüklenebildiği ayrıca doğrulanır.
3. TRUBA hesaplama modunu açın. Bir **SLURM yetenek kontrolü** çalışır: Linux
   Python/Rust çekirdeği, Netgen, Kratos uygulamaları ve uyumlu OpenCFD komutları
   hesaplama düğümünde kontrol edilir. Kuyruk beklerken işlemler hazır sayılmaz.
4. Çalışma ortamı yoksa **Prepare remote runtime** düğmesine açıkça basın.
   Bu işlem ayrı bir SLURM işiyle kullanıcıya ait
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
   Bağlantı veya çalışma ortamı değişirse **Check remote capabilities** ile yeniden
   doğrulayın. Kontrol ve kurulum işleri kendi Stop düğmesiyle iptal edilebilir.

TRUBA modunda mevcut destek:

| İşlem | Yürütme |
| --- | --- |
| Model ve DoE sınır geometrisi | Geometri ve FE ağı TRUBA'da; yüzey/mesh yerelde çizilir |
| Mekanik analiz | Doğrulanmış Kratos adaptörü; önizlemedeki aynı uzak FE ağı yeniden kullanılır |
| Mesh convergence | Her seviyenin mesh, çözüm ve metrikleri TRUBA'da; sıradaki seviyeyi seçen hafif arayüz mantığı istemcide |
| DoE mesh kontrolü / analiz | Satırın mesh ve çözüm işlemleri TRUBA'da |
| Flow (CFD) | Akış ağı, OpenFOAM çözümü ve sonuç hazırlama TRUBA'da |
| Thermal (CHT) | Su/katı ağı, zamana bağlı CHT ve 3B karelerin hazırlanması TRUBA'da |
| İçe aktarılan STL/VTK parçası | Hizalanmış yüzey yüklenir; TET4/TET10 ağ TRUBA'da, desteklenen TET4 basma çözümü Kratos ile |
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
elle girilen sayı tahsisi aşarsa analiz başlamaz. HEX20/TET10, uzak ortamın mesh
kontrolünden geçmişse önizleme için seçilebilir; Kratos çözümü için HEX8/TET4
seçilmelidir. Mesh üretme hakkı ile çözücünün eleman desteği ayrı denetlenir.

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

Durum: ertelendi; şimdilik uygulanmayacak. Hedef, voxel yüzeyindeki basamaklı
çıkıntıları gerçek referans geometri sınırına göre kesmek. Tek bağlantılı
elemanların silinmesi bu hedefi karşılamaz. Önce küçük bir örnekte kesilmiş
hücrelerin eleman tipleri, çok küçük hücreler, hacim farkı ve ANSYS aktarımı
değerlendirilecek. Saf HEX20 korunacaksa sınır düğümlerini yüzeye oturtma yolu
ayrıca eleman bozulması açısından incelenecek. Uygulamaya geçiş sonraki iş.
Bu fikir `More methods` menüsünde de yalnız plan olarak listelenir.

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
View menüsündedir. Mesh Convergence ve DoE işlevleri korunur; planlanan yöntemler
More methods menüsündedir.

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
Örneğin `72:00:00` üç günlük tahsis ister; kuyruğun ve hesabınızın bu süreyi ve
kaynak miktarını kabul etmesi gerekir. İlk tahsis Slurm kuyruğunda bekler.
**Refresh session** kayıtlı oturumun durumunu ve kalan süresini yeniler.

Oturum hazır olduğunda TRUBA hesaplama modunu etkinleştirin ve
**Check remote capabilities** ile ortamı doğrulayın; gerekirse
**Prepare remote runtime** kullanın. Bu kontroller de aynı tahsiste çalışır.
Ardından ağ üretimi ve analizler, yeni `sbatch` tahsisi açmadan aynı kaynağın
`srun` adımları olarak sırayla yürütülür. Önceki analiz çalışıyorsa yeni analiz
uygulamanın oturum kuyruğunda bekler. Kaynak ayarları oturum boyunca sabittir.

Boşta geçirilen süre de tahsis süresine dahildir. Süre bittiğinde devam eden
hesaplama durdurulabilir; uygulama kendiliğinden yeni tahsis açmaz. Bir analizi
iptal etmek kaynak oturumunu kapatmaz. **Release resources** oturumu ve içindeki
işleri sonlandırır. RustMech'i kapatmak tahsisi serbest bırakmaz; yeniden açınca
aynı Windows kullanıcısına ait kayıt **Refresh session** ile doğrulanabilir.
Bağlantı hatasında kayıtlı işi yenileyin veya geri alın; tekrar kaynak talep
ederek belirsiz bir gönderimin kopyasını oluşturmayın.

Oturum protokolü ve iptal kontrolleri:
`python -m unittest tests.test_truba_session tests.test_truba_remote tests.test_remote_mode_ui`.

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
This import workflow applies to mechanical compression; flow and thermal analyses
continue to require generated structures.


## Bağımlılıklar ve sürüm kontrolü

`requirements-base.txt`, çalışan geliştirme ortamındaki (Python 3.14.10) temel paket
sürümlerini kaydeder; temiz ortamda kurulum henüz denenmedi. İsteğe bağlı gruplar
`requirements-truba.txt`, `requirements-tet10.txt` ve `requirements-mapdl.txt`
dosyalarındadır. Proje git deposundadır; kullanıcı sonuçları (`HumanResults/`,
`results/`), `dist*/`, `build/`, ikililer ve kök günlükleri `.gitignore` ile dışarıda
tutulur. Eski kök günlükleri `build/logs/2026-11-kok-arsivi/` altına taşındı;
`RustMech.log` çalışma günlüğü olarak kökte kalır.

### Test grupları

`python tools/run_tests.py core` ekran gerektirmeyen testleri (Qt offscreen), `gui`
ise VTK/OpenGL penceresi açan testleri gerçek masaüstü oturumunda çalıştırır;
`--python` ile yorumlayıcı seçilir. Her dosya ayrı süreçte ve süre sınırıyla koşar,
tümü başarılıysa çıkış kodu 0'dır. `RUSTMECH_OFFLINE=1` varsayılandır (loopback SSH
testi atlanır). Yeni bir pencere testi offscreen modda VTK pixel-format hatasıyla
düşerse `tools/run_tests.py` içindeki `GUI` listesine eklenmelidir.
