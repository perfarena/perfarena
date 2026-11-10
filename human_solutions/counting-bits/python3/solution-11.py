def countBits(n):
    ar = [0] * (n + 1)
    base = 1
    for i in range(1, n + 1):
        if i == base * 2:
            base = i
        ar[i] = ar[i - base] + 1
    return ar