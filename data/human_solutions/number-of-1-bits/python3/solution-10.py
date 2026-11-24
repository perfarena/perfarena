def count_ones_in_binary(num):
    count = 0
    while num > 0:
        if num & 1 == 1:
            count += 1
        num >>= 1
    return count

# Test the function
print(count_ones_in_binary(6))  # Output should be 2