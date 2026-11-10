# Capture the length of the given array
n = len(nums)

# Calculate the prefix sum array
prefix = [0] * n
prefix[0] = nums[0]
for i in range(1, n):
    prefix[i] = prefix[i-1] + nums[i]

# Initialize an answer array
answer = [0] * n

# Calculate for each number in the array
for i in range(n):
    left_sum = prefix[i-1] if i > 0 else 0
    right_sum = prefix[n-1] - nums[i]

    left_count = i
    right_count = n - i - 1

    left_answer = nums[i] * left_count - left_sum
    right_answer = right_sum - nums[i] * right_count

    answer[i] = left_answer + right_answer

# Return the final answer
return answer