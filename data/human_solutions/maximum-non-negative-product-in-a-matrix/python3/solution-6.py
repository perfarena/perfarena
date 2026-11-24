# Regular Iteration Approach
def calculate_money_regular(n):
    account = 0
    for i in range(1, n + 1):
        account += i
        if i % 7 == 0:
            account -= (i // 7) * 7
    return account

# Mathematical Solution Approach
def calculate_money_mathematical(n):
    weeks = n // 7
    days = n % 7
    total_sum = (n * (n + 1)) // 2
    full_weeks_sum = (weeks * (weeks + 1) * 7) // 2
    remaining_sum = total_sum - full_weeks_sum
    remaining_days_sum = (days * (days + 1)) // 2
    return full_weeks_sum + remaining_sum - remaining_days_sum

# Test the functions
n = 25
result_regular = calculate_money_regular(n)
result_mathematical = calculate_money_mathematical(n)
print("Regular Iteration Approach Result:", result_regular)
print("Mathematical Solution Approach Result:", result_mathematical)