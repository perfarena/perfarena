def signOfProduct(arr):
    sign = 1
    for num in arr:
        if num == 0:
            return 0
        elif num < 0:
            sign *= -1
    return sign

# Test the function
print(signOfProduct([2, -6]))  # Output: 1