for num in nums:
    if num <= 0 or num > len(nums):
        nums[num - 1] = len(nums) + 1