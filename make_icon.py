"""Generate the DVR Viewer icon: a bullet CCTV camera on a blue tile."""
from PIL import Image, ImageDraw

S = 1024                      # supersampled working size
BLUE_TOP    = (58, 138, 255)
BLUE_BOTTOM = (17,  70, 168)
WHITE       = (255, 255, 255, 255)
LENS_DARK   = (13, 17, 28, 255)
LENS_BLUE   = (46, 122, 232, 255)
RED         = (255, 70, 70, 255)


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1],
                                        radius=radius, fill=255)
    return m


# --- background: vertical gradient inside a rounded square ---------------
bg = Image.new("RGBA", (S, S))
d = ImageDraw.Draw(bg)
for y in range(S):
    t = y / (S - 1)
    d.line([(0, y), (S, y)],
           fill=tuple(int(a + (b - a) * t) for a, b in
                      zip(BLUE_TOP, BLUE_BOTTOM)) + (255,))
bg.putalpha(rounded_mask(S, 205))

# --- camera body + lens on their own layer, then rotate ------------------
cam = Image.new("RGBA", (S, S), (0, 0, 0, 0))
c = ImageDraw.Draw(cam)
BODY = [235, 430, 780, 640]
c.rounded_rectangle(BODY, radius=105, fill=WHITE)
# lens housing at the front (left) end
c.ellipse([190, 398, 420, 672], fill=WHITE)
c.ellipse([232, 440, 378, 630], fill=LENS_DARK)
c.ellipse([262, 470, 348, 600], fill=LENS_BLUE)
c.ellipse([286, 492, 318, 528], fill=(180, 214, 255, 255))   # glint
# sunshade ridge along the top of the body
c.rounded_rectangle([300, 402, 700, 452], radius=25, fill=WHITE)
# recording LED near the back
c.ellipse([690, 500, 742, 556], fill=RED)
cam = cam.rotate(15, resample=Image.BICUBIC, center=(512, 535))

# --- mount arm + ceiling plate, drawn upright over the tilted body -------
mount = Image.new("RGBA", (S, S), (0, 0, 0, 0))
m = ImageDraw.Draw(mount)
m.rounded_rectangle([600, 250, 672, 500], radius=34, fill=WHITE)
m.rounded_rectangle([505, 196, 768, 268], radius=34, fill=WHITE)

out = Image.alpha_composite(bg, mount)
out = Image.alpha_composite(out, cam)

out.resize((512, 512), Image.LANCZOS).save("icon_preview.png")
sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
out.resize((256, 256), Image.LANCZOS).save("dvr_viewer.ico",
                                           format="ICO", sizes=sizes)
print("wrote dvr_viewer.ico and icon_preview.png")
