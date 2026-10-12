def make_elements_equal(nums):
    nums.sort(reverse=True)
    CNT = 0
    for i in range(1, len(nums)):
        if nums[i] < nums[i - 1]:
            CNT += i
    return CNT

# Test the function with the input array
nums = [5, 3, 1]
result = make_elements_equal(nums)
print(result)