def maxProduct(nums):
    a = nums
    b = nums[::-1]

    for x in range(1, len(nums)):
        a[x] *= a[x - 1] or 1
        b[x] *= b[x - 1] or 1

    return max(a + b)

# Test the function with the given input
nums = [2, 3, -2, 4]
result = maxProduct(nums)
print(result)