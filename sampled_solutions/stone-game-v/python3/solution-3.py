def calculate_absolute_differences(nums):
    n = len(nums)
    sum_nums = sum(nums)
    answer = [0] * n
    current_sum = 0

    for i in range(n):
        left_sum = current_sum - i * nums[i]
        right_sum = sum_nums - current_sum - (n - i) * nums[i]
        answer[i] = left_sum + right_sum
        current_sum += nums[i]

    return answer