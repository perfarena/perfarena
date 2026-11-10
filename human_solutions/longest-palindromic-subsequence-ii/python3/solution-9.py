def determine_sign_of_product(arr):
    sign = 1
    for num in arr:
        if num < 0:
            sign *= -1
    return sign

# Test the function
test_array = [2, -3, 5, 0]
result = determine_sign_of_product(test_array)
print(result)  # Output should be -1