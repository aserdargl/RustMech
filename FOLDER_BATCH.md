# Klasörden sıralı mekanik analiz

1. Ana **Mechanic** çalışma alanında malzeme, ağ, yükleme, temas ve çözücü
   parametrelerini ayarlayın.
2. **Designs** yanındaki **Batch Analysis** düğmesine tıklayın ve klasörü seçin.
   STL, OBJ, PLY ve VTP dosyaları alt klasörleriyle birlikte, dosya adına göre
   doğal sırada taranır (`part2`, `part10` öncesindedir). Batch çalışma
   klasörleri, eski `Result` klasörleri ve bağlantılı klasörler tarama dışında tutulur.
   Koordinatlar mm kabul edilir.
3. Basma yönünü seçin: X, Y, Z; ilk numunede seçilen destek/yükleme
   yüzeylerinden yön alma; veya her numunede elle seçim.
4. **Check folder** ile ön kontrolü çalıştırın. Malzeme ve analiz ayarlarının
   kopyası bu anda alınır. Kopuk bölgelerin toplam yüzey alanı ana bileşenin
   yüzey alanının %1'ini aşmıyorsa küçük bölgeler otomatik ayıklanır.
   Daha büyük bölgeler için kalacak bileşeni elle seçin. Ayıklama analiz
   geometrisine uygulanır ve kaynak dosya korunur. Otomatik mod, numunenin uç sınırlarında
   düz ve paralel yüzeyler arar. TPMS ağında dış uçta yaygın bir temas bölgesi
   hafif dalgalıysa, ön kontrol ilgili köşeleri analiz kopyasında en fazla
   numune yüksekliğinin %1'i kadar dış düzleme taşıyarak yüzeyi düzleştirir.
   Numunenin yüksekliği korunur ve işlem ön kontrol sütununda gösterilir.
   Aynı düzlemdeki birden fazla temas yaması
   birlikte seçilir. Yön bilgisi aktarılır; üçgen kimlikleri aktarılmaz.
5. Numune satırındaki kutu simgesinin üzerine gelince mesnet ve basma yüzeyleri
   iki açıdan görünür. Düzenleyicideki değişiklikler kaydedilince önizleme yenilenir.
   Kutu düğmesiyle açılan 3B görünümde **Connected components** listesinden
   kalacak tek katıyı seçebilirsiniz. Mavi bileşen
   kalır, kırmızı bileşenler ayıklanır. Destek yeşil, yükleme turuncudur.
   Bir yüzey atamasını değiştirmek için rolü seçin ve modeldeki yüzeye sağ
   tıklayın. Seçimleri uygulamak için **Save and close** düğmesine basın. Kaydedilen
   atamalar, kaynak dosya değişmediği sürece sonraki ön kontrollerde de
   kullanılır. Hazır olmayan numuneleri hazırlayın veya çalışmanın dışında bırakın.
6. Çözücü olarak mevcut çalışma alanını, ANSYS MAPDL, Kratos veya her ikisini
   seçin. Aynı parametreler kullanılır; çözücünün desteklemediği malzeme, temas
   veya eleman seçimi açık hata verir, sessizce değiştirilmez. İçe aktarılan
   numunelerin hacim ağı Netgen ile oluşturulur.
7. **Mesh** bölümünde genel ve yerel ağ boyutlarını (mm) ve eleman türünü
   (**TET4** veya **TET10**) ayarlayın. Başlangıç değerleri Mechanic çalışma
   alanından gelir. Geometri ön kontrolünden sonra da bu değerleri
   değiştirebilirsiniz. Kuyruğa eklenen her iş, o anda seçilen ağ ayarlarını saklar.
   **Add to Queue** seçin. TRUBA hesaplama modu açıkken düğme **Add Truba**
   olarak görünür. TRUBA'da Kratos basma için **TET4** kullanın.
   Çözücü uyumluluğu ve analiz ayarları doğrulanır.
   Kaydedilen işler sağdaki **Analysis Queue** sütununa taşınır. Aynı kaynak
   içeriğine ve ayarlara sahip işler yeniden eklenmez. Kaynak dosya hazırlıktan
   sonra değişmişse yeniden ön kontrol gerekir.
   Bir işi geri almak için sağdaki satırı seçip **Remove from Queue** düğmesine
   tıklayın. Numune soldaki listede tekrar seçilir.
8. **Analysis Queue > Run analysis queue** ile açıkça başlatın. Her
   numunenin seçili çözücüleri tamamlandıktan sonra sıradaki numuneye geçilir.
   Varsayılan hata politikası sonraki işe devam eder. Tamamlanan işler atlanır;
   hatalı işler aynı kuyruktan yeniden denenebilir. Yeniden açılışta kesilmiş
   işler işaretsizdir; çalıştırmadan önce durumlarını kontrol edin.

Hazırlık dosyaları ve yeni işlerin sonuçları seçilen klasördeki `BatchAnalysis`
altında tutulur. Eski `BatchAnaylsis` çalışma klasörleri taşınırken, kuyruktaki
geometri ve sonuç yolları da güncellenir. Ayrı `Result` klasörlerine bağlı eski
işler kayıtlı konumlarını kullanmaya devam eder.

```text
Numuneler/
  numune_01.stl
  BatchAnalysis/
    Prepared/  # Analize hazırlanan VTP kopyaları ve kaydedilen seçimler
    Images/    # Yüzey önizleme PNG dosyaları
    Results/
      toplu_ozet.csv
      numune_01-<kimlik>/Mechanical/ansys_mapdl/JOB-<kimlik>/
        job.json
        Run_<zaman>/
          summary.json
          01_Geometry/
          02_MeshQuality/
          03_Results/
          04_Graphs/
          Raw/
```

`job.json` kaynak özetini, yönü, ayarları, durumu ve sonuç ölçümlerini içerir;
başarısız işler de raporlanır. Çözücünün ürettiği geometri, alan sonuçları,
eğri CSV dosyaları ve ham dosyalar korunur. Her deneme ayrı çalışma klasörü
kullanır. Aynı isimli farklı numuneler birbirinin çıktısını ezmez. TRUBA'da
mevcut çalışma alanının çözücüsü kullanılır ve indirilen sonuçlar ayrıca bu
klasöre kopyalanır; kopya başarısızsa özgün indirme korunur ve hata gösterilir.

Küp şekli veya izotropi varsayımı otomatik seçim şartı değildir. Kullanıcı
fiziksel basma yönünü belirler. Eğik kaydedilmiş numunelerde ilk numuneden yön
alma veya elle seçim kullanılmalıdır. Düz temas yüzeyi olmayan bir numune için
program yaklaşık yüzey tahmin etmez; geometri düzeltmesi veya farklı bir temas
modeli gerekir. Yük büyüklüğü ölçeklenmez: deplasman mm, kuvvet N olarak aynı
kalır. Pencere boyutu, konumu ve büyütülmüş durumu kullanıcıya özel saklanır.

Bu klasör kuyruğu mekanik basma içindir. Akış ve termal çalışmaların mevcut
ayrı iş akışları korunmuştur.
