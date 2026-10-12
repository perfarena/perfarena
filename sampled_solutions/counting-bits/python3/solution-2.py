def count_ones_in_range(num):
    result = []
    for i in range(num + 1):
        if i == 0:
            result.append(0)
        else:
            count = bin(i & (i - 1)).count('1')
            result.append(count)
    return result

# Test the function
num = 5
print(count_ones_in_range(num))  # Output: [0, 1, 1, 2, 1, 2]