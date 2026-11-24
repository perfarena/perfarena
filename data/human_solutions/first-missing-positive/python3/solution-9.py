for index in range(len(nums)):
    if nums[index] > 0:
        return index + 1

return len(nums) + 1