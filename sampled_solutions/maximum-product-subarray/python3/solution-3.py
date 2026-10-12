def maxProductSubarray(nums):
    if not nums:
        return 0

    prefix = 1
    suffix = 1
    max_product = float('-inf')

    for i in range(len(nums)):
        prefix *= nums[i]
        suffix *= nums[len(nums) - 1 - i]

        max_product = max(max_product, prefix, suffix)

        if prefix == 0:
            prefix = 1
        if suffix == 0:
            suffix = 1

    return max_product

# Example usage
nums = [2, 3, -2, 4]
result = maxProductSubarray(nums)
print(result)