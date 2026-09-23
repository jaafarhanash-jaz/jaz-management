import { Card } from '@/components/ui/card';
import { ShieldOff } from 'lucide-react';
import { ts } from '@/utils/salesTranslations';

// Shown when the signed-in user reaches a Sales section they hold no
// permission for (e.g. by typing the URL). Purely presentational - the API
// would answer 403 anyway.
const SalesNoAccess = ({ language }) => (
  <Card className="p-12 text-center bg-white border border-gray-200" data-testid="sales-no-access">
    <ShieldOff className="w-12 h-12 mx-auto text-gray-300 mb-4" />
    <h2 className="text-lg font-semibold text-[#0A0A0A]">{ts('no_access_title', language)}</h2>
    <p className="text-gray-500 mt-2">{ts('no_access_body', language)}</p>
  </Card>
);

export default SalesNoAccess;
