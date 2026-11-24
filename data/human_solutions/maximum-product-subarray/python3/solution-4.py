def find_maximum_product(numbers):
    max_product = float('-inf')
    n = len(numbers)
    
    for i in range(n):
        for j in range(i+1, n):
            product = numbers[i] * numbers[j]
            max_product = max(max_product, product)
    
    return max_product

# Test the function
numbers = [2, 3, 4, 5]
result = find_maximum_product(numbers)
print("Maximum Product:", result)