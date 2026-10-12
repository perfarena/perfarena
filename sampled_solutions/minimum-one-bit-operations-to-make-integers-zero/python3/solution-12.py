# Initialize variables
answer = 0
prefix_sum = 0

# Iterate through altitude gains
for gain in gains:
    prefix_sum += gain
    answer = max(answer, prefix_sum)

# Return the highest altitude reached
return answer