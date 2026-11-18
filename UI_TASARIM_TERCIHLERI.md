# RustMech arayüz tasarım tercihleri

- Masaüstü arayüzü için ana referans Datenburg’un GraphCalc Edit penceresi. RustMech’te ortak görünüm `studio_style.py`, `datenburg_widgets.py` ve `assets/ui/datenburg.qss` üzerinden uygulanır.
- Açılır listenin dört kenarında, açık zeminden ayırt edilebilen 1 px çerçeve olsun. Satırlar kompakt kalsın, uzun seçenekler tam okunsun ve klavye odağı görünür olsun.
- Panel gruplarını açık gri, 1 px çizgilerle ayır. Üzerine gelindiğinde çizginin rengi değişebilir. Fareyle sürükleme alanı rahat kullanılacak genişlikte kalsın.
- Düğmeler ve açılır kutular içeriklerine göre kompakt boyutlansın. İlişkili işlem düğmeleri aynı genişlik ve yükseklikte olsun. Düğme yüzeyleri düz, kenarları ince, gölgeleri hafif olsun.
- Pencere boyutu, konumu, büyütülmüş durumu ve kullanıcının ayarladığı panel oranları sonraki açılışta geri yüklensin.
