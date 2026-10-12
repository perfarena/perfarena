def calculate_total_amount(n):
    answer = 0
    for i in range(n+1):
        week = i // 7
        day_in_week = i % 7
        if day_in_week == 0:
            answer += 1 + week
        else:
            answer += 1 + week + day_in_week
    return answer

# Test the function
n = 10
print(calculate_total_amount(n))  # Output should be 56