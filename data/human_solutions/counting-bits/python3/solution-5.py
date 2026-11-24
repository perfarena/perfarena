def count_bits(n):
    result = [0]
    while len(result) <= n:
        result.extend([i + 1 for i in result])
    return result[:n + 1]

n = 15
result = count_bits(n)
print(result)