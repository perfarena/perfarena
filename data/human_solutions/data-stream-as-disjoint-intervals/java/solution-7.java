public class Main {
    public static void main(String[] args) {
        DestroyedIntervals di = new DestroyedIntervals();
        int[] dataStream = {1, 2, 3, 5, 6};
        int[][] intervals = di.getDestroyedIntervals(dataStream);

        for (int[] interval : intervals) {
            System.out.println("[" + interval[0] + ", " + interval[1] + "]");
        }
    }
}