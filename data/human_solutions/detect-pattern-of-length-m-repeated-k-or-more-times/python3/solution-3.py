def min_binary_numbers(n):
    return max(n, *[int(x) for x in str(n)])

# Example usage
n = 123
result = min_binary_numbers(n)
print(result)