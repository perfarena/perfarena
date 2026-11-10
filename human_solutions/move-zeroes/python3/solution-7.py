last_zero = -1
for i, num in enumerate(nums):
    if not num:  # If current element is zero
        if last_zero == -1:
            last_zero = i
        else:
            nums[i], nums[last_zero] = nums[last_zero], nums[i]
    elif last_zero != -1:  # If current element is non-zero
        nums[i], nums[last_zero] = nums[last_zero], nums[i]
        last_zero += 1