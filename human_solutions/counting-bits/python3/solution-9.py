def countBits(num):
    result = [0] * (num + 1)
    for i in range(num + 1):
        result[i] = result[i >> 1] + (i & 1)
    return result