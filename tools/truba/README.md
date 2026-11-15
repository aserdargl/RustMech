# TRUBA bağlantısı

VPN'i açın, RustMech üst çubuğundaki **TRUBA** düğmesine basın ve kendi
TRUBA kullanıcı adınız ile şifrenizi girin. **Connect** düğmesine açıkça
basılmadan bağlantı başlamaz; alanlarda Enter yalnızca düzenlemeyi tamamlar.
**Show password** ile yazdığınız şifreyi kontrol edebilirsiniz. Panel gizlenince
veya bağlantı başlatılınca şifre yeniden maskelenir. Bu tercih kaydedilmez.
Hedef `172.16.6.15:22` adresidir. İlk bağlantıda sunucu parmak izini TRUBA'nın
güvenilir bilgileriyle doğrulayıp onaylayın. Değişen sunucu anahtarları reddedilir.

Başarılı kimlik doğrulamasından sonra üst çubuktaki düğme yeşil **Connected**
olur. Bağlantı kaybında normal görünümüne döner; VPN/network kayıplarında
yeniden bağlanmayı dener. Yanlış şifre sürekli tekrar denenmez.
**Disconnect** veya RustMech'ten çıkış bağlantıyı durdurur.

**Remember password for this Windows user** isteğe bağlıdır ve yalnızca
başarılı girişten sonra kaydeder. Bilgiler Windows DPAPI'nin kullanıcı kapsamıyla
şifrelenerek `%LOCALAPPDATA%\RustMech\TRUBA\credentials.dpapi` dosyasına
yazılır. Şifre program dizinine, settings.json'a veya dağıtım paketine yazılmaz.
Başka bir Windows kullanıcısı kopyalanan dosyayı çözemez. Aynı Windows hesabını
kullanan kişiler kayıtlı girişi kullanabilir; ortak bilgisayarda ayrı Windows
hesapları kullanın veya şifre hatırlamayı kapatın.

Kutucuğu kapatmak kaydı siler. **Forget saved password** hem kaydı hem alandaki
şifreyi temizler. Sunucu anahtarları aynı kullanıcı dizinindeki `known_hosts`
dosyasında saklanır. Pencere boyutu ve konumu mevcut kullanıcı tercihlerine tabidir.

Arayüz Paramiko kullanır (`requirements-truba.txt`); yerel SSH anahtarlarını
veya SSH Agent'ı kullanmaz. Eski manuel yardımcı PowerShell betikleri arayüzden
çağrılmaz; kullanılacaksa açıkça `-Username kendi_kullanici_adiniz` ister ve her
TRUBA kullanıcı adı için ayrı yerel SSH anahtarı kullanır.

Bağlantı durumunda sunucu parmak izi onayı, kullanıcı doğrulaması, şifre reddi,
şifreyle girişin desteklenmemesi, zaman aşımı ve SSH görüşmesi hataları ayrı
gösterilir. Sunucu kimliği onaylanmadan kullanıcı doğrulaması başlamaz.
SSH görüşmesi hatalarında yalnız hata sınıfı gösterilir; şifreler veya ham
sunucu hata metinleri kaydedilmez. WinSCP ile karşılaştırmak için SFTP,
`172.16.6.15`, port `22` ve aynı TRUBA hesabını kullanın.

## Kratos kullanıcı kurulumu

`install-kratos.sh`, Python 3.12 modülünü kullanarak yönetici yetkisi olmadan
`$HOME/.local/share/RustMech/kratos-10.4.4` altında bağımsız bir ortam oluşturur.
Sistem Python'unu veya kabuk başlangıç dosyalarını değiştirmez. Kratos çekirdeği,
StructuralMechanics, ContactStructuralMechanics, ConstitutiveLaws ve LinearSolvers
paketleri 10.4.4 sürümüne sabitlenmiştir. Betik bağımlılıkları ve modüllerin
yüklenmesini doğrular; bir analiz veya SLURM işi başlatmaz.

Bu hesapta kurulum ve beş paketin yüklenmesi doğrulandı. Yerel doğrulama kaydı:
`build/truba-kratos-installation.json`. Ortamı kullanmadan önce:

```bash
module load comp/python/3.12.0
$HOME/.local/share/RustMech/kratos-10.4.4/bin/python -m pip check
```

RustMech'te çözücü seçilince sol panel değişir. ANSYS'in mevcut sayısal temas
ayarları korunur; Kratos için lineer çözücü, mortar yöntemi, Newton iterasyonları,
toleranslar ve line-search ayarları ayrı tutulur. Geometri ve malzeme ortaktır.
Çözücüye özgü ağ, yükleme ve temas profilleri kullanıcıya özel Qt ayarlarında
kaydedilir; son seçilen çözücü uygulama açılışında geri yüklenir. Kratos temas
akışında şimdilik HEX8/TET4 seçilir; HEX20/TET10 seçimi ANSYS'te kullanılabilir.

## Analiz gönderme ve sonuç alma

Çözücü olarak **Kratos** seçin. TRUBA alanlarında kuyruk (varsayılan `debug`),
süre, bellek ve çekirdek sayısını hesabınızın sınırlarına göre düzenleyin.
Hesap alanı boşsa bağlanan kullanıcı adı kullanılır. OpenMP kullanılır;
çok düğümlü MPI gönderimi yapılmaz.

Bağlantı yöntemi **Auto** ise açık RustMech SSH oturumu veya bu Windows
kullanıcısının kayıtlı girişi kullanılır. Bunlar yoksa TRUBA için WinSCP'de
kayıtlı oturum kullanılır. Birden çok oturum varsa adını Kratos panelindeki
WinSCP alanına yazın. WinSCP'nin açık kalması yeterli değildir: yeni SSH/SFTP
oturumları için VPN'in ve kayıtlı girişin çalışması gerekir. RustMech WinSCP
parolasını okumaz veya kendi dosyalarına kopyalamaz.

**Run analysis** mevcut HEX8/TET4 ağını değiştirmeden MDPA'ya aktarır,
benzersiz `/arf/scratch/<kullanıcı>/RustMech/<kimlik>` dizinine yükler ve `sbatch`
ile gönderir. İş kimliği/durumu arayüzde görünür. Hesaplama giriş düğümünde
çalıştırılmaz. İş bitince düğüm alanları, platen reaksiyonları ve günlükler
indirilir; mevcut CSV/VTU ve kuvvet–yer değiştirme raporları üretilir.
**View latest results** adım, gerilme, yer değiştirme ve deformasyon ölçeği
seçilebilen sonuç penceresini açar. Pencere boyutu/konumu kullanıcıya göre saklanır.

Bağlantı kesilirse **Retrieve TRUBA job…** ile
`%LOCALAPPDATA%\RustMech\TRUBA\jobs` altındaki iş kaydını seçin. Aynı yerel
ham veri dizini korunmalıdır. İş aynı uzak dizinden izlenir/indirilir;
ikinci kez gönderilmez. Gönderimin sunucuda kabul edilip edilmediği belirsizse
güvenli biçimde durur ve kopya iş oluşturmaz. **Force stop** uzak işi iptal eder,
sonra yerel çalışan süreci durdurur. Uzak iptal doğrulanamazsa hata gösterir.
Başarıyla bitmiş ve tamamen indirilmiş işler yerel kopyadan açılabilir;
raporu yeniden üretmek veya alanları görüntülemek için SSH girişi gerekmez.
Eksik indirme ya da eksik SLURM muhasebe bilgisi varsa geri alma yeniden bağlanır.

Sonuçlar kabul edilmeden ağ koordinatları/bağlantıları, düğüm kimlikleri,
son yük adımı, SLURM iş kimliği ve çıkış durumu kontrol edilir. Yakınsamayan
adımlar tamamlanmış sayılmaz; mevcut kısmi sonuçlar açıkça işaretlenir.

Doğrulanan temel kapsam: doğrusal elastisite, bilinear/multilinear izotropik
plastisite, deplasman/kuvvet kontrollü statik basma, rijit düz plakalarla
sürtünmesiz/sürtünmeli temas, HEX8/TET4. ALM için SparseLU önerilen varsayılandır;
AMGCL penalty ile kullanılabilir. Line search kapalı tutulur. Küçük yerel gerçek
çözücü kontrolleri `tools/verify_kratos_pipeline.py` ile tekrarlanabilir.
Bu kontroller, ağ inceldikçe plaka kenarlarında temasın kaybolmaması için
64/216 HEX8 ve daha sık TET4 küp örneklerini de içerir. Temas arama yarıçapı,
rijit plaka merkeziyle her köle yüzün boyutu/konumu dikkate alınarak hesaplanır.

## Öz-temas

Kratos seçiliyken **Internal self-contact** alanında sürtünmesiz veya Coulomb
sürtünmeli öz-teması seçebilirsiniz. Bu akış **HEX8 + Penalty mortar** kullanır;
seçim yapıldığında temas yöntemi penalty olarak güncellenir. TET4 öz-teması ve
rough/no-sliding öz-teması henüz desteklenmez; TET4 plaka teması kullanılabilir.

**Penalty factor** büyütüldükçe penetrasyon azalır, ancak yakınsama zorlaşabilir.
Öz-temas sabit yük artımları ve kısıtlarla uyumlu deplasman yakınsama ölçütü
kullanır. Advanced alanındaki **Increments** sayısını artırarak daha küçük
adımlar seçin. Otomatik adım sınırları bu modda kullanılmaz. Plaka kuvvet dengesi
ayrıca denetlenir; %1 üzerindeki dengesizlik veya başarısız yük adımı tamamlanmış
sonuç olarak kabul edilmez.

Numunenin hacim ağı değiştirilmez. Temas yüzlerinin yardımcı düğümleri hacim
düğümlerine tam kinematik kısıtlarla bağlanır. Komşu yüzler öz-temas çifti yapılmaz;
yaklaşan karşı yüzlerin rolleri kararlı tutulur. Belirsiz temas birleşiminde veya
temas taraflarının ters dönmesi gerektiğinde analiz açık hata ile durur; ağ/yük
adımlarını inceltin. Plaka ve öz-temas sürtünme katsayıları ayrı özelliklerde tutulur.

Sonuç penceresinde **Self-contact pressure / MPa** ve **Self-contact shear / MPa**
alanları seçilebilir. Bunlar orijinal hacim düğümlerindeki en büyük aktif köle-yüz
değeridir. Aynı alanlar VTU dosyalarına da yazılır; aktif düğüm, basınç ve Coulomb
sınırı bilgileri her yakınsayan adımın kaydında bulunur.

`tools/verify_kratos_self_contact.py`, başlangıç açıklığı 0,05 mm olan tek parça
boşluklu numuneyi 0,07 mm sıkıştırır. Açık/kapalı öz-temas, kuvvet dengesi,
penetrasyon ve Coulomb sınırı birlikte kontrol edilir. Geliştirme ortamında:

```text
python tools/verify_kratos_self_contact.py disabled
python tools/verify_kratos_self_contact.py frictionless --remote
python tools/verify_kratos_self_contact.py frictional --shear --remote
python tools/verify_kratos_self_contact.py frictional --shear --mu 0.0001 --remote
```

Son komut düşük sürtünmeyle kayma sınırını da sınar. `--resume <remote_job.json>`
aynı işin sonuçlarını kullanır; tamamlanmış ve indirilmiş işler için ağ bağlantısı
gerekmez. Bu küçük deneyler genel bir malzeme/mesh yakınsama çalışmasının yerine geçmez.
