# Sort intervals based on start time
intervals.sort(key=lambda x: x[0])

result = []
start = intervals[0][0]
end = intervals[0][1]

for interval in intervals[1:]:
    if interval[0] > end:
        result.append([start, end])
        start = interval[0]
        end = interval[1]
    else:
        end = max(end, interval[1])

result.append([start, end])

return result