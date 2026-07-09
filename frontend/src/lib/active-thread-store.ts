import { create } from "zustand";

type ActiveThreadStore = {
  activeThreadId: string | undefined;
  setActiveThreadId: (id: string | undefined) => void;
};

export const useActiveThreadStore = create<ActiveThreadStore>((set) => ({
  activeThreadId: undefined,
  setActiveThreadId: (id) => set({ activeThreadId: id }),
}));
