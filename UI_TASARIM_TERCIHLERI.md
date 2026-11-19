# RustMech arayüz tasarım tercihleri

- Masaüstü arayüzü için ana referans Datenburg’un GraphCalc Edit penceresi. RustMech’te ortak görünüm `studio_style.py`, `datenburg_widgets.py` ve `assets/ui/datenburg.qss` üzerinden uygulanır.
- Açılır liste, düğmeyi açıkta bırakacak şekilde hemen altından açılsın. Ekranın alt kenarında yeterli yer kalmazsa düğmenin üstüne doğru açılsın. Listenin dört kenarında, açık zeminden ayırt edilebilen 1 px çerçeve olsun. Satırlar kompakt kalsın, uzun seçenekler tam okunsun ve klavye odağı görünür olsun.
- Panel gruplarını açık gri, 1 px çizgilerle ayır. Üzerine gelindiğinde çizginin rengi değişebilir. Fareyle sürükleme alanı rahat kullanılacak genişlikte kalsın.
- Her analiz için sol sütunda, ayarların altında ayrı bir malzeme seçim grubu kullan. Analiz, malzeme seçimini ve özelliklerini kendi panelinden alsın. Bir analizdeki değişiklik diğer analizlerin atamalarını değiştirmesin.
- Düğmeler ve açılır kutular içeriklerine göre kompakt boyutlansın. İlişkili işlem düğmeleri aynı genişlik ve yükseklikte olsun. Düğme yüzeyleri düz, kenarları ince, gölgeleri hafif olsun.
- Pencere boyutu, konumu, büyütülmüş durumu ve kullanıcının ayarladığı panel oranları sonraki açılışta geri yüklensin.
