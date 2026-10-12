def count_ones(n):
    array = [0] * (n + 1)
    for num in range(len(array)):
        array[num] = bin(num).count('1')
    return array

# Test the function with n equals two
n = 2
output = count_ones(n)
print(output)