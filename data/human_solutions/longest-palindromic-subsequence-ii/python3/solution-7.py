zero_count = 0
negative_count = 0

for num in nums:
    if num < 0:
        negative_count += 1
    elif num == 0:
        zero_count += 1

if zero_count > 0:
    return 0
elif negative_count % 2 == 0:
    return 1
else:
    return -1