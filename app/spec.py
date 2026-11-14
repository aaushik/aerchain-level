"""Box weight from the specification. This is the bridge between "Rs/kg" quotes and "Rs/box" lines.

It is an engineering estimate (blank area x combined board grammage), and the UI says so wherever it is used.
"""

GSM = {("rsc", 3): 380, ("rsc", 5): 720, ("mailer", 3): 360, ("pad", 3): 380, ("pad", 5): 720}


def est_weight_g(kind, ply, L, W, H, board_spec=""):
    L, W, H = L or 0, W or 0, H or 0
    ply = 5 if ply == 5 else 3
    spec = (board_spec or "").lower()
    if kind == "rsc" and L and W and H:
        g = GSM[("rsc", ply)]
        if "140/120/140" in spec:
            g *= 1.12
        if "180/150" in spec:
            g *= 1.2
        return round((2 * L + 2 * W + 35) * (W + H) / 1e6 * g)       # RSC blank: (2L+2W+glue flap) x (W+H)
    if kind == "mailer" and L and W and H:
        return round((L + 4 * H + 20) * (2 * W + 3 * H + 20) / 1e6 * GSM[("mailer", 3)])
    if kind == "pad" and L and W:
        return round(L * W / 1e6 * GSM[("pad", ply)])
    if kind == "partition":
        cells = 12 if "12" in spec else 6
        return 240 if cells == 12 else 95
    return None                                                       # rolls are bought by weight already


def weight_basis(line):
    k = line.get("kind")
    if k == "rsc":
        return "RSC blank area (2L+2W+35) x (W+H) x combined board grammage"
    if k == "mailer":
        return "die-cut blank area x E-flute board grammage"
    if k == "pad":
        return "L x W x board grammage"
    if k == "partition":
        return "typical set weight for this cell count"
    return ""
