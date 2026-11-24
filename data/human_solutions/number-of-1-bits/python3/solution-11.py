def count_ones_in_binary(num):
    count = 0
    while num != 0:
        count += num & 1
        num >>= 1
    return count

# Example usage
binary_num = 13  # Binary representation: 1101
num_of_ones = count_ones_in_binary(binary_num)
print("Number of ones in the binary representation:", num_of_ones)