export type XnoEnvelope<T> = {
  success?: boolean;
  data: T;
  message?: string;
  status_code?: number;
  statusCode?: number;
  pagination?: { page?: number; limit?: number; total?: number };
};
