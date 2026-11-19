"""Clipboard instructions for preparing a RustMech ESEN job package."""

ESEN_AI_PROMPT = """\
# RustMech ESEN için LAMMPS dosyalarımı hazırla

Bu yönergeyle birlikte verdiğim dosyaları incele ve seçtiğim çalıştırma biçimine
uygun bir iş klasörü hazırla. Hedef, LAMMPS sürümü, TRUBA modülü veya model için
zorunlu bir dosya belirsizse gerekli bilgiyi benden iste. Birimleri, malzemeyi,
kuvvet alanını, atom tiplerini, sınır koşullarını ve analiz adımlarını koru.
Eksik veri, potansiyel, hesap adı veya kaynak değerleri uydurma.

## ESEN işi nasıl çalıştırır?

- Tek işte ESEN girdilerin bir çalışma kopyasını oluşturur ve ana betiği
  `lmp -in <ana-betik>` biçiminde çalıştırır. Çalışma dizini ESEN’de seçilen iş
  köküne karşılık gelir. Ana betik alt klasörde olsa bile yolları iş köküne göre
  yaz. Ana betiği `in.model` veya `model.in` gibi adlandır. Birden çok aday
  varsa hangi dosyanın “Betik Olarak Seç” ile seçileceğini belirt.
- Yerel çalıştırmada kullanıcının Windows LAMMPS kurulumuna uygun komutları ve
  paketleri kullan. Birden çok MPI süreci için LAMMPS ile uyumlu MPI başlatıcısı
  gerekir. Komut ve başlatıcı ESEN alanlarından ayarlanır.
- TRUBA’ya LAMMPS girdi betiği gönderildiğinde ESEN Slurm betiğini üretir. LAMMPS sürümü/modülü,
  kuyruk ve süre gibi gereken ESEN ayarlarını listele. Klasörden toplu işte
  her işin çalışma dizini seçilen ana betiğin bulunduğu klasördür. Gerekli
  bağımlılıkları iş klasörüne koy. TRUBA’da bu modda önce klasörü aktar,
  ardından çalıştırılacak LAMMPS betiklerini işaretle.
- TRUBA’da hazır Slurm betiği için `.slurm` uzantısını kullan. Seçilen klasörde
  `.slurm` dosyası varsa TRUBA’da çalıştırılacak öğeler Slurm betikleridir.
  Dosyayı UTF-8 ve LF satır sonlarıyla kaydet. İlk satır `#!/bin/bash -l`
  gibi geçerli bir yorumlayıcı satırı olsun. `#SBATCH` satırlarında gerçek
  kuyruk, hesap, görev sayısı, bellek ve süreyi kullan. Uyumlu LAMMPS modülünü
  yükle ve uygun MPI komutuyla `lmp -in` çağır. ESEN iş adını ve standart günlük
  yollarını ayarlar. Her betik kendi klasörüne karşılık gelen ayrı bir çalışma
  kopyasında yürür. İşaretli işler ESEN listesindeki sırayla gönderilir. Bir
  işin çıktısının sonraki işin kopyasında kendiliğinden bulunacağını varsayma.

## Dosya ve klasör kuralları

- Ana ve yardımcı LAMMPS betiklerini düz metin, tercihen ASCII uyumlu ve
  BOM’suz kaydet. `read_data`, `include`, `pair_coeff`, `read_restart` ve
  benzeri komutların kullandığı tüm dosyaları dahil et. Her dosyanın biçimi onu
  okuyan LAMMPS komutuna uygun olsun. Örneğin `read_data` için LAMMPS data başlığı,
  kutu sınırları, doğru sayımlar ve `atom_style` ile uyumlu `Atoms` sütunları
  gerekir. Potansiyelin özgün biçimini ve `pair_coeff` element sırasını koru.
  İkili ara kayıtları metne dönüştürme. Üreten LAMMPS derlemesini kullan.
- Yolları çalışma dizinine göre ve `/` ile yaz. Sürücü harfi, mutlak yol,
  kök dışına çıkan yol, sembolik bağlantı ve Windows’ta geçersiz dosya adı
  kullanma. Linux’ta adların büyük/küçük harfleri birebir eşleşsin. Örneğin
  iş kökünde `in.model`, yanında `data/model.data` ve `potentials/` bulunabilir.
  `in.model` içindeki veri komutu `read_data data/model.data` olur.
- ESEN’de bağımlılıkları ana betikle birlikte ekle veya “İş klasörünün tamamını
  ekle” seçeneğini kullan. Alt klasör yapısını koru. Çıktı yolları göreli olsun
  ve gereken çıktı klasörleri yazma işleminden önce mevcut olsun. Girdi
  dosyalarının üzerine yazma. Başarılı TRUBA işlerinin sonuçları bağlantı
  açıkken yerel iş klasöründeki `Results/ESEN-<iş-kimliği>` altına indirilir.
- Betiğin aşamalarını ESEN’de göstermek için standart çıktıya `ESEN_STEP=` ile
  başlayan satırlar ekle. LAMMPS’te `print "ESEN_STEP=Minimizasyon" screen yes`
  komutunu kullanabilirsin.

## İsteğe bağlı TRUBA ara kayıt desteği

ESEN’in oluşturduğu Slurm betiğiyle devam özelliği isteniyorsa başlangıç ve
devam betiklerinde `restart 10000 ${esen_checkpoint_dir}/step.*` gibi tek
dosyalı, adım numaralı ara kayıtlar yaz. Devam betiğinde
`read_restart ${esen_checkpoint}` komutunu kullan. Gerekli fix ve çıktı ayarlarını
yeniden kur. `run <son-adım> upto` ile mutlak hedef adıma kadar devam et. Çıktı
adlarında `${esen_segment}` kullan. Devam betiğinin çalışma dizinine göre yolunu
ESEN’de “Devam girdisi” alanına yazmam için belirt.

## Teslim biçimi

Klasör ağacını, ana betiği, düzenlediğin metin dosyalarının tam içeriğini ve
aynen tutulacak veri/potansiyel dosyalarını ver. Gerekli ESEN seçimlerini ve
yapılan değişiklikleri listele. Dosyalar arasındaki yolları ve biçimleri kontrol
et. Gerçek ortamda deneme yapmadıysan bunu belirt ve gerekiyorsa kısa bir
doğrulama çalıştırmasının nasıl yapılacağını yaz.

## LAMMPS biçim belgeleri
https://docs.lammps.org/stable/Commands_parse.html
https://docs.lammps.org/read_data.html
https://docs.lammps.org/read_restart.html
"""
