def week_sum(XS, days):
    XF = XS + days - 1
    return (XS + XF) * days // 2

def total_money_saved(n):
    full_weeks = n // 7
    remaining_days = n % 7
    total = 0
    for week in range(full_weeks):
        total += week_sum(1, 7)
    total += week_sum(1, remaining_days)
    return total

# Test the function
print(total_money_saved(7))  # Output should be 28
print(total_money_saved(4))  # Output should be 10