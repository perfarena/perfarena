def find_pivot_index(nums):
    total_sum = sum(nums)
    left_sum = 0
    right_sum = total_sum

    for i in range(len(nums)):
        number = nums[i]
        right_sum -= number

        if left_sum == right_sum:
            return i

        left_sum += number

    return -1