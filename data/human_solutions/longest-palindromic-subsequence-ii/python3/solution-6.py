def signProduct(nums):
    if len(nums) < 1:
        return None

    neg_count = 0
    for num in nums:
        if num == 0:
            return 0
        if num < 0:
            neg_count += 1

    if neg_count % 2 == 1:
        return -1
    else:
        return 1

# Test the function
nums = [-1, -2, -3, -4, -6, -4, -3, -2, -1]
result = signProduct(nums)
print(result)