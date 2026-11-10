def countBits(num):
    bitCounts = [0] * (num + 1)
    for i in range(1, num + 1):
        bitCounts[i] = bitCounts[i >> 1] + (i & 1)
    return bitCounts