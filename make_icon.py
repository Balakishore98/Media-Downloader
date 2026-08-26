"""Generate icon.ico from the MediaForge brand mark."""
from PIL import Image, ImageDraw

BG, ACCENT, DIM = (14, 20, 27, 255), (0, 229, 192, 255), (10, 125, 108, 255)
S = 512
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=BG)

def scale(points, factor=S / 30.0, dx=0.0, dy=0.0):
    return [((x + dx) * factor, (y + dy) * factor) for x, y in points]

# hexagon shell
hexagon = [(3, 4), (15, 4), (21, 15), (15, 26), (3, 26), (9, 15)]
d.line(scale(hexagon, dx=1.4, dy=2.0) + [scale(hexagon, dx=1.4, dy=2.0)[0]],
       fill=ACCENT, width=int(S * 0.045), joint='curve')
# play triangle
d.polygon(scale([(12, 10), (12, 21), (21, 15.5)], dx=1.4, dy=2.0), fill=ACCENT)
# signal dashes
for y, length in ((7, 3), (15, 5), (23, 3)):
    d.line(scale([(24, y), (24 + length, y)], dx=1.4, dy=2.0), fill=DIM,
           width=int(S * 0.042))

img.save('icon.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                            (128, 128), (256, 256)])
img.resize((256, 256), Image.LANCZOS).save('docs/icon.png')
print('icon.ico + docs/icon.png written')
