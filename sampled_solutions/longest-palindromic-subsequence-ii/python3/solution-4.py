def array_sign(nums):
    sign = 1  # Initially set to positive
    for num in nums:
        if num == 0:
            return 0
        if num < 0:
            sign *= -1
    return sign

# Test the function
nums = [-1, -2, -3]
print(array_sign(nums))  # Output should be -1