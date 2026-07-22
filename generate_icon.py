"""Regenere icon.ico en multi-resolution (16/32/48/256 px), a partir d'un
dessin vectoriel reproduisant fidelement le motif de l'icone actuelle (document
plie en coin, corps blanc, bandeau rouge avec etiquette claire) - voir
l'analyse pixel par pixel de l'ancien icon.ico (16x16 unique) dans le rapport
d'audit, dimension 19.

Rendu a haute resolution (2048px) avec anti-aliasing (LANCZOS au downscale),
puis decline vers chaque taille cible, pour un rendu net a toutes les echelles
(barre des taches, Explorateur en grandes icones, raccourci bureau) plutot que
l'agrandissement pixelise d'une unique image 16x16.
"""
from PIL import Image, ImageDraw

# Couleurs reprises telles quelles de l'icone actuelle (verifie par sonde
# pixel sur icon.ico existant : (200,50,40) corps rouge, (180,40,30) bordure/
# bandeau fonce, (250,250,250) blanc quasi pur).
RED_BODY = (200, 50, 40, 255)
RED_DARK = (180, 40, 30, 255)
WHITE = (250, 250, 250, 255)
TRANSPARENT = (0, 0, 0, 0)

CANVAS = 2048


def build_master() -> Image.Image:
    img = Image.new("RGBA", (CANVAS, CANVAS), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    margin = int(CANVAS * 0.065)
    left, top = margin, margin
    right, bottom = CANVAS - margin, CANVAS - margin
    fold = int((right - left) * 0.42)  # taille du coin plie, proportionnelle au motif original

    # Corps du document (rectangle avec coin superieur droit coupe en diagonale),
    # dessine en une seule bordure fonce pleine, puis rempli en blanc a
    # l'interieur (meme structure que l'original : bordure R, remplissage W).
    border_w = int(CANVAS * 0.028)
    document = [
        (left, top),
        (right - fold, top),
        (right, top + fold),
        (right, bottom),
        (left, bottom),
    ]
    draw.polygon(document, fill=RED_DARK)

    inner = [
        (left + border_w, top + border_w),
        (right - fold, top + border_w),
        (right - border_w, top + fold),
        (right - border_w, bottom - border_w),
        (left + border_w, bottom - border_w),
    ]
    draw.polygon(inner, fill=WHITE)

    # Bandeau rouge (partie basse), au meme ratio que l'original (rangees
    # 9-12 sur 16, hors bordures -> ~34%-78% de la hauteur utile).
    band_top = top + border_w + int((bottom - top - 2 * border_w) * 0.56)
    band_bottom = top + border_w + int((bottom - top - 2 * border_w) * 0.80)
    draw.rectangle([left + border_w, band_top, right - border_w, band_bottom], fill=RED_BODY)

    # Etiquette claire en bas du bandeau (ligne blanche fine, meme motif que
    # la rangee 13 de l'original).
    label_top = top + border_w + int((bottom - top - 2 * border_w) * 0.86)
    label_bottom = top + border_w + int((bottom - top - 2 * border_w) * 0.93)
    draw.rectangle([left + border_w, label_top, right - border_w, label_bottom], fill=WHITE)

    return img


def main():
    master = build_master()
    sizes = [16, 32, 48, 256]
    # Pillow derive chaque taille demandee par miniaturisation (LANCZOS) de
    # l'image de BASE passee a save() - elle doit donc etre au moins aussi
    # grande que la plus grande taille demandee (256px), sans quoi les
    # tailles superieures a l'image de base sont silencieusement ignorees
    # (verifie empiriquement : passer une image de base 16x16 ne produisait
    # plus qu'une seule taille dans le fichier final, malgre `sizes=`).
    base = master.resize((256, 256), Image.LANCZOS)

    out_path = "icon.ico"
    base.save(out_path, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"Ecrit {out_path} avec les tailles {sizes}")


if __name__ == "__main__":
    main()
