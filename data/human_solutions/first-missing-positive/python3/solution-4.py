def first_missing_positive(nums):
    for i in range(len(nums)):
        if nums[i] <= 0:
            nums[i] = 0

    for i in range(len(nums)):
        num = abs(nums[i])
        if num != 0 and num <= len(nums):
            nums[num - 1] = -abs(nums[num - 1])

    for i in range(len(nums)):
        if nums[i] >= 0:
            return i + 1

    return len(nums) + 1