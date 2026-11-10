def arraySign(nums):
    sign = 1
    for num in nums:
        if num == 0:
            return 0
        elif num < 0:
            sign *= -1
    return sign

# Test the function with the provided input
nums = [-1, -2, -3, -4, 3, 2, 1]
result = arraySign(nums)
print(result)