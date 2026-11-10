function mergeIntervals(intervals) {
    intervals.sort((a, b) => a[0] - b[0]);
    let result = [intervals[0]];

    for (let i = 1; i < intervals.length; i++) {
        let [start, end] = intervals[i];
        let [prevStart, prevEnd] = result[result.length - 1];

        if (prevEnd >= start) {
            result[result.length - 1][1] = Math.max(prevEnd, end);
        } else {
            result.push([start, end]);
        }
    }

    return result;
}