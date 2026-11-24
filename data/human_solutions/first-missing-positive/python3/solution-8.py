for num in nums:
    if num <= len(nums):
        if nums[num - 1] >= 0:
            nums[num - 1] *= -1