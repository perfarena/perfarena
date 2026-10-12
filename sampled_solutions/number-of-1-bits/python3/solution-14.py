def count_one_bits_brute_force(n):
    binary_str = bin(n)[2:]  # Convert to binary string
    result = 0
    for char in binary_str:
        if char == '1':
            result += 1
    return result