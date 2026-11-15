"""Turkish hover help for geometry and mesh settings."""

GEOMETRY_HELP = {
    "tetra_strategy": (
        "Auto, uygun tam periyotlu TPMS ve Truss Lattice yapılarda bir hücreyi meshleyip çoğaltır; "
        "eşleşen yüzler, eleman kalitesi ve referans hacim farkını kontrol eder. "
        "Uygun olmayan yapılarda veya eleman bütçesi aşıldığında Netgen'e döner. "
        "Periodic cells, uygun değilse açıklama gösterir. "
        "Hücre yolu yerel boyutu bütün hücreye uygular; çok ince yerel boyutlar pahalıdır. "
        "Netgen'den daha fazla eleman ve daha düşük şekil kalitesi üretebilir. "
        "6 mm / 3 mm Gyroid için local 0,10 mm ile denenebilir."
    ),
    "structure_family": (
        "Yapı ailesini seçer: TPMS, çubuk kafes (Truss Lattice) veya gözenekli (Porous). "
        "Yapı tipi listesi bu seçime göre değişir."
    ),
    "structure_name": (
        "Seçili aile içindeki geometrik deseni belirler; örneğin Gyroid. "
        "Aynı boyut ve katı oranında farklı tipler farklı duvar ve bağlantı düzenleri üretir."
    ),
    "tpms_morphology": (
        "Katının yapıda nasıl dağıldığını seçer. Sheet, yüzey çevresinde duvar oluşturur; "
        "Network A ve B, yapının birbirini tamamlayan iki tarafını katı olarak kullanır."
    ),
    "size": "Küp numunenin bir kenarının uzunluğu (mm). Toplam model boyutunu belirler.",
    "cell_size": (
        "Periyodik yapılarda birim hücre periyodu, gözenekli yapılarda karakteristik "
        "gözenek ölçeğidir (mm). Küçüldükçe numuneye daha fazla hücre veya gözenek sığar. "
        "Küp boyutunu aşamaz."
    ),
    "solid_fraction": (
        "Numune hacminin katı malzemeyle doldurulması hedeflenen yüzdesidir. "
        "Örneğin %30 katı oranı, yaklaşık %70 boşluk anlamına gelir. "
        "Üretilen mesh'in gerçek katı oranı ayrıklaştırma nedeniyle farklı olabilir."
    ),
    "mesh_resolution": (
        "Genel sonlu eleman boyutudur (mm). TET4/TET10 için global üst boyut sınırı, "
        "HEX8/HEX20 için voxel aralığının hedefidir; HEX aralığı küpe sığacak şekilde ayarlanır. "
        "Küçük değer daha fazla eleman, daha fazla bellek ve daha uzun üretim/çözüm süresi getirir."
        " Referans geometri otomatik hazırlanır ve mesh boyutuyla değişmez. "
        "Kaba mesh geometriyi daha köşeli temsil edebilir."
    ),
    "local_mesh_size": (
        "Yalnız TET4/TET10 için yerel hedef eleman boyutudur (mm). "
        "Alt/üst tabla temas yüzeylerinde ve örneklenmiş yüzeyde algılanan ince duvarlarda "
        "(kalınlığı bu değerin yaklaşık 5 katına kadar) mesh'i inceltir. "
        "Temas ve ince duvar ayrıntılarının temsilini iyileştirmeye yardımcı olur. "
        "Global FE mesh size değerini aşamaz. Örneğin global 0,20 mm, local 0,05 mm. "
        "Küçültmek eleman sayısını, bellek ihtiyacını ve üretim/çözüm süresini artırabilir; "
        "tek başına çözüm doğruluğunu garanti etmez. Yeni projelerde varsayılan 0,05 mm'dir. "
        "Periyodik hücre yöntemi yerel boyutu tüm hücreye uygular."
    ),
    "mesh_type": (
        "Sonlu eleman tipini seçer. HEX8/HEX20, voxel tabanlı 8/20 düğümlü hexahedron; "
        "TET4/TET10, yüzeyi izleyen 4/10 düğümlü tetrahedron kullanır. "
        "HEX20 ve TET10 ikinci derecedir ve daha fazla düğüm içerir. "
        "Local mesh size yalnız TET tiplerinde kullanılır."
    ),
    "random_seed": (
        "Rastgele gözenekli yapı üretiminin başlangıç sayısıdır. "
        "Diğer ayarlar aynıyken aynı sayı aynı geometriyi üretir; "
        "farklı sayı farklı bir gözenek düzeni oluşturur."
    ),
}
