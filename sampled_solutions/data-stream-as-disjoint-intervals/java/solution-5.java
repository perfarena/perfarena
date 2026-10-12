class Interval {
    int start;
    int end;

    public Interval(int start, int end) {
        this.start = start;
        this.end = end;
    }
}

class SummaryRanges {
    List<Interval> getSummaryRanges(int[] nums) {
        List<Interval> result = new ArrayList<>();
        if (nums == null || nums.length == 0) {
            return result;
        }

        int start = nums[0];
        int end = nums[0];

        for (int i = 1; i < nums.length; i++) {
            if (nums[i] == end + 1) {
                end = nums[i];
            } else {
                result.add(new Interval(start, end));
                start = nums[i];
                end = nums[i];
            }
        }

        result.add(new Interval(start, end));

        return result;
    }
}