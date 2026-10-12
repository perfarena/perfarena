# Sort the array
arrays.sort(nums)
# Capture the length of the array
n = len(nums)
# Initialize a variable to store the answer
answer = 0

# Check for all pairs
for i in range(n // 2):
    # Calculate the maximum sum for the current pair
    max_sum = nums[i] + nums[n - 1 - i]
    # Update the answer if the current sum is greater
    answer = max(answer, max_sum)

# Return the final answer
return answer