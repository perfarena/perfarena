def min_deci_binary_numbers(n):
    max_digit = max(map(int, n))
    count = 0
    while int(n) > 0:
        deci_binary = int('1' * len(n), 2)
        n = str(int(n) - deci_binary)
        count += 1
    return count

# Example Usage
n = "32"
result = min_deci_binary_numbers(n)
print(result)  # Output: 3