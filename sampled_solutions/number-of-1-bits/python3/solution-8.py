def count_one_bits(n):
    cons = 0
    for i in range(32):
        mask = 1 << i
        if n & mask != 0:
            cons += 1
    return cons