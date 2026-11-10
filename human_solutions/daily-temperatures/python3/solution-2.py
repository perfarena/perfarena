stack = []
res = [0] * len(temperatures)

for curr_day, curr_temp in enumerate(temperatures):
    while stack and curr_temp > temperatures[stack[-1]]:
        prev_day = stack.pop()
        res[prev_day] = curr_day - prev_day
    stack.append(curr_day)

return res